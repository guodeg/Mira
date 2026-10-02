"""Tonghuashun (hithink-finance) CLI adapter -> canonical ``market_price`` / ``company_financials``.

Reads A-share data on demand through the locally authenticated ``hithink-finance``
CLI. The vendor API key lives in the CLI's own credential store and is never
copied into Mira config, tracked files or logs; Mira only records the connector
contract (``private/connector-registry.yaml``).

Evidence tier: L5 for both families (arch doc §7).

- A-share quotes and forward-adjusted daily bars are **market data**, not a
  fundamentals fact source (``market_price_and_trading``).
- The financial statements are **aggregated** vendor data
  (``aggregated_financial_data``): the source taxonomy requires a cross-check
  against issuer / CNINFO / exchange disclosures (L1/L2) before any durable
  conclusion. Vendor licence terms are unverified, so the shipped posture keeps
  raw payloads in private state with ``redistribution_allowed=unknown``.

Vendor field map — A-share statements use Chinese labels that do **not** match the
English/US-GAAP labels the SEC adapter emits:

    vendor field                          canonical metric
    operating_income    (营业总收入)        revenue
    operating_costs     (营业成本)          operating_costs
    operating_profit    (营业利润)          operating_income
    net_profit          (净利润)            net_income
    parent_holder_net_profit (归母净利润)   net_income_attributable_to_parent
    research_and_development_expenses     rnd_expense
    assets_total        (资产总计)          total_assets
    total_debt          (负债合计)          total_liabilities
    holder_equity_total (所有者权益合计)    stockholders_equity
    cash                (货币资金)          cash_and_equivalents
    act_cash_flow_net   (经营现金流净额)    operating_cash_flow
    pay_fixed_assets_etc_cash             capex
    basic_eps           (基本每股收益)      basic_eps

Two traps this map exists to prevent:

1. The vendor's ``operating_income`` is **total operating revenue**, not
   operating profit; operating profit arrives as ``operating_profit``.
2. The vendor's ``total_debt`` is **total liabilities** (负债合计), not
   interest-bearing debt: it satisfies ``total_debt + holder_equity_total =
   assets_total``. Interest-bearing debt is not disclosed by these endpoints and
   is therefore emitted as a data gap, not guessed.

``gross_profit`` is not disclosed either, so it is emitted as a Mira-derived
number (``revenue - operating_costs``) and therefore carries a ledger row.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import subprocess
from typing import Optional

from .. import config, net
from ..canonical import POSTURES, CanonicalRecord, FetchResult

DEFAULT_BIN = "hithink-finance"
DEFAULT_TIMEOUT = 60.0
SNAPSHOT_ENDPOINT = "hithink-finance://market.snapshot/{thscode}"
FINANCIALS_ENDPOINT = "hithink-finance://financials/{thscode}"
SERIES_COLUMNS = ["date", "open", "high", "low", "close", "volume"]
TRADING_DAYS_52W = 252

# The vendor encodes calendar dates as an epoch at **Beijing midnight**
# (checked: every date-like field satisfies ms % 86_400_000 == 57_600_000, i.e.
# 16:00Z). Decoding those with UTC silently moves every trading date, period end
# and filing date one day early, so all conversion goes through Asia/Shanghai.
# China has no DST, so a fixed +08:00 offset is exact and dependency-free
# (zoneinfo would need a tz database that Windows does not ship).
CN_TZ = _dt.timezone(_dt.timedelta(hours=8))

# A-share money fields are quoted in CNY; B-share boards are left out on purpose
# until a real B-share symbol is requested (resolve_thscode would route 900xxx/200xxx).
_BOARD_BY_LEADING_DIGIT = {"6": "SH", "0": "SZ", "3": "SZ", "4": "BJ", "8": "BJ", "9": "BJ"}
_BOARD_CURRENCY = {"SH": "CNY", "SZ": "CNY", "BJ": "CNY"}

# (canonical_metric, vendor_field, vendor_label)
INCOME_FIELDS = [
    ("revenue", "operating_income", "营业总收入"),
    ("operating_costs", "operating_costs", "营业成本"),
    ("operating_income", "operating_profit", "营业利润"),
    ("net_income", "net_profit", "净利润"),
    ("net_income_attributable_to_parent", "parent_holder_net_profit", "归属于母公司股东的净利润"),
    ("rnd_expense", "research_and_development_expenses", "研发费用"),
]
BALANCE_FIELDS = [
    ("total_assets", "assets_total", "资产总计"),
    ("total_liabilities", "total_debt", "负债合计"),
    ("stockholders_equity", "holder_equity_total", "所有者权益合计"),
    ("cash_and_equivalents", "cash", "货币资金"),
]
CASH_FLOW_FIELDS = [
    ("operating_cash_flow", "act_cash_flow_net", "经营活动产生的现金流量净额"),
    ("capex", "pay_fixed_assets_etc_cash", "购建固定资产、无形资产和其他长期资产支付的现金"),
]
EXTRA_FIELDS = [("basic_eps", "basic_eps", "基本每股收益", "per_share")]


# --------------------------------------------------------------------------- #
# CLI plumbing
# --------------------------------------------------------------------------- #

def resolve_thscode(symbol: str) -> str:
    """Map a user symbol to a vendor thscode.

    Accepts ``600519.SH`` (vendor), ``SH.600519`` (Futu style) or a bare 6-digit
    A-share code, whose board is inferred from the leading digit. No remote
    lookup: the mapping is deterministic so the adapter stays offline-testable.
    """
    text = (symbol or "").strip().upper()
    if not text:
        raise net.FetchError("invalid_symbol: empty symbol")
    if "." in text:
        left, right = text.split(".", 1)
        if left in _BOARD_CURRENCY and right.isdigit():
            return f"{right}.{left}"
        if right in _BOARD_CURRENCY and left.isdigit():
            return f"{left}.{right}"
        raise net.FetchError(
            f"invalid_symbol: {symbol!r} is not an A-share symbol "
            "(expected 600519.SH, SH.600519 or a bare 6-digit code)")
    if len(text) != 6 or not text.isdigit():
        raise net.FetchError(f"invalid_symbol: {symbol!r} is not a 6-digit A-share code")
    board = _BOARD_BY_LEADING_DIGIT.get(text[0])
    if board is None:
        raise net.FetchError(f"invalid_symbol: no A-share board inferred for {symbol!r}")
    return f"{text}.{board}"


def resolve_bin() -> str:
    """Resolve the CLI entry point (``MIRA_HITHINK_BIN`` overrides PATH lookup)."""
    configured = (config.get("MIRA_HITHINK_BIN") or "").strip()
    if configured:
        return configured
    for name in (DEFAULT_BIN, f"{DEFAULT_BIN}.cmd", f"{DEFAULT_BIN}.exe"):
        found = shutil.which(name)
        if found:
            return found
    raise net.FetchError(
        "hithink_cli_gap: hithink-finance CLI not found; install it or set MIRA_HITHINK_BIN")


def _timeout() -> float:
    raw = (config.get("MIRA_HITHINK_TIMEOUT") or "").strip()
    try:
        return float(raw) if raw else DEFAULT_TIMEOUT
    except ValueError:
        return DEFAULT_TIMEOUT


def _argv(binary: str, args: list[str]) -> list[str]:
    # npm ships .cmd/.ps1 shims on Windows; CreateProcess cannot start a .cmd
    # directly, so route it through the command interpreter without shell=True.
    if os.name == "nt" and binary.lower().endswith((".cmd", ".bat")):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", binary, *args]
    return [binary, *args]


def _run(args: list[str], *, timeout: Optional[float] = None) -> dict:
    """Run one CLI call and return its ``data`` object."""
    binary = resolve_bin()
    argv = _argv(binary, args)
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout or _timeout(),
        )
    except FileNotFoundError as exc:
        raise net.FetchError(f"hithink_cli_gap: could not execute {binary}") from exc
    except subprocess.TimeoutExpired as exc:
        raise net.FetchError(
            f"hithink_timeout: {' '.join(args)} exceeded {timeout or _timeout()}s") from exc

    payload = _parse_envelope(proc.stdout, args, proc.returncode)
    if not payload.get("ok"):
        error = payload.get("error") or {}
        detail = "; ".join(
            str(part) for part in (error.get("code"), error.get("message"), error.get("hint"))
            if part
        ) or f"exit code {proc.returncode}"
        raise net.FetchError(f"hithink_error: {' '.join(args)}: {detail}")
    return payload.get("data") or {}


def _parse_envelope(stdout: str, args: list[str], returncode: int) -> dict:
    text = (stdout or "").strip()
    if not text:
        raise net.FetchError(f"hithink_bad_output: no output from {' '.join(args)} (exit {returncode})")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise net.FetchError(f"hithink_bad_output: {' '.join(args)} did not return a JSON envelope")


def probe() -> dict:
    """Check the CLI binary, version and authentication state."""
    binary = resolve_bin()
    version = _run(["version", "--format", "json"]) or {}
    auth = _run(["auth", "status", "--format", "json"]) or {}
    return {
        "binary": binary,
        "package": version.get("package"),
        "version": version.get("version"),
        "node": version.get("node"),
        "auth_method": auth.get("method"),
        "auth_profile": auth.get("profile"),
        "auth_configured": bool(auth.get("configured")),
    }


# --------------------------------------------------------------------------- #
# market_price
# --------------------------------------------------------------------------- #

def fetch_market_price(
    symbol: str,
    *,
    as_of: Optional[str] = None,
    market_scope: str = "CN",
    lookback_days: int = 400,
) -> FetchResult:
    """Snapshot + forward-adjusted daily bars for one A-share."""
    as_of = as_of or _dt.date.today().isoformat()
    thscode = resolve_thscode(symbol)
    currency = _currency_for(thscode)
    posture = POSTURES["hithink_finance_market"]

    snapshot = _run(["market", "snapshot", "--thscodes", thscode, "--format", "json"])
    quotes = snapshot.get("item") or []
    if not quotes:
        raise net.FetchError(f"hithink_source_gap: no snapshot rows for {thscode}")
    quote = quotes[0]

    end = _dt.date.today()
    start = end - _dt.timedelta(days=lookback_days)
    history = _run([
        "market", "history", "--thscode", thscode,
        "--start-ms", str(_to_ms(start)), "--end-ms", str(_to_ms(end)),
        "--format", "json",
    ])
    rows = sorted(
        (bar for bar in (_bar(row) for row in history.get("item") or []) if bar["close"] is not None),
        key=lambda bar: bar["date"],
    )
    if not rows:
        raise net.FetchError(f"hithink_source_gap: no usable daily bars for {thscode}")

    window = rows[-TRADING_DAYS_52W:]
    highs = [bar["high"] for bar in window if bar["high"] is not None]
    lows = [bar["low"] for bar in window if bar["low"] is not None]
    last_date = rows[-1]["date"]
    url = SNAPSHOT_ENDPOINT.format(thscode=thscode)
    provenance = {
        "thscode": thscode,
        "interval": history.get("interval") or "1d",
        "adjust": history.get("adjust") or "forward",
        "snapshot_time": _ms_to_time(snapshot.get("timestamp")),
        "bars": len(rows),
    }

    def quote_record(metric, value, unit):
        if value is None:
            return None
        return CanonicalRecord(
            family="market_price", research_object=thscode, market_scope=market_scope,
            metric=metric, value=value, unit=unit,
            currency=currency if unit == currency else None,
            period=last_date, period_type="point_in_time", as_of_date=as_of,
            source_date=last_date, posture=posture, url_or_path=url, provenance=provenance,
        )

    last_close = quote.get("last_price")
    if last_close is None:
        last_close = rows[-1]["close"]
    records = [
        quote_record("last_close", last_close, currency),
        quote_record("fifty_two_week_high", max(highs) if highs else None, currency),
        quote_record("fifty_two_week_low", min(lows) if lows else None, currency),
        quote_record("last_volume", quote.get("volume") or rows[-1]["volume"], "shares"),
    ]
    records = [record for record in records if record is not None]
    series = {"name": f"market_price-{thscode}", "columns": SERIES_COLUMNS, "rows": rows}
    return FetchResult(records, series=series)


def _bar(row: dict) -> dict:
    return {
        "date": _ms_to_date(row.get("date_ms")),
        "open": _num(row.get("open_price")),
        "high": _num(row.get("high_price")),
        "low": _num(row.get("low_price")),
        "close": _num(row.get("close_price")),
        "volume": _num(row.get("volume")),
    }


# --------------------------------------------------------------------------- #
# company_financials
# --------------------------------------------------------------------------- #

def fetch_company_financials(
    symbol: str,
    *,
    as_of: Optional[str] = None,
    market_scope: str = "CN",
    period: str = "annual",
    limit: int = 5,
) -> FetchResult:
    """Latest income statement, balance sheet and cash-flow statement as claims."""
    as_of = as_of or _dt.date.today().isoformat()
    thscode = resolve_thscode(symbol)
    posture = POSTURES["hithink_finance_financials"]

    income = _latest(_statement("income", thscode, period, limit), thscode, "income")
    balance = _latest(_statement("balance-sheet", thscode, period, limit), thscode, "balance-sheet")
    cash = _latest(_statement("cash-flow", thscode, period, limit), thscode, "cash-flow")

    currency = income.get("currency") or balance.get("currency") or cash.get("currency") \
        or _currency_for(thscode)
    label = _period_label(income)
    source_date = _statement_date(income)
    url = FINANCIALS_ENDPOINT.format(thscode=thscode)

    def record(metric, value, unit, vendor_field, vendor_label, stmt):
        if value is None:
            return None
        return CanonicalRecord(
            family="company_financials", research_object=thscode, market_scope=market_scope,
            metric=metric, value=value, unit=unit,
            currency=currency if unit == currency else None,
            period=label, period_type="fiscal_period", as_of_date=as_of,
            source_date=source_date, posture=posture, url_or_path=url,
            provenance={
                "vendor_field": vendor_field, "vendor_label": vendor_label,
                "report_period": stmt.get("period"), "fiscal_year": stmt.get("fiscal_year"),
                "fiscal_period": stmt.get("fiscal_period"),
                "period_end": _period_end(stmt), "announced": _statement_date(stmt),
            },
        )

    records = []
    for statement, fields in ((income, INCOME_FIELDS), (balance, BALANCE_FIELDS), (cash, CASH_FLOW_FIELDS)):
        for metric, vendor_field, vendor_label in fields:
            records.append(record(metric, _num(statement.get(vendor_field)), currency,
                                  vendor_field, vendor_label, statement))
    for metric, vendor_field, vendor_label, kind in EXTRA_FIELDS:
        unit = f"{currency}/share" if kind == "per_share" else currency
        records.append(record(metric, _num(income.get(vendor_field)), unit,
                              vendor_field, vendor_label, income))

    revenue = _num(income.get("operating_income"))
    operating_costs = _num(income.get("operating_costs"))
    if revenue is not None and operating_costs is not None:
        records.append(CanonicalRecord(
            family="company_financials", research_object=thscode, market_scope=market_scope,
            metric="gross_profit", value=revenue - operating_costs, unit=currency, currency=currency,
            period=label, period_type="fiscal_period", as_of_date=as_of, source_date=source_date,
            posture=posture, url_or_path=url, derived=True,
            upstream_sources=f"{posture.source_id}:{thscode}",
            formula="gross_profit = operating_income (营业总收入) - operating_costs (营业成本)",
            cross_check="single_source",
            provenance={"vendor_field": "operating_income - operating_costs", "vendor_label": "毛利"},
        ))

    records = [record_ for record_ in records if record_ is not None]
    if not records:
        raise net.FetchError(f"hithink_source_gap: no curated statement fields for {thscode}")
    return FetchResult(records)


def _statement(kind: str, thscode: str, period: str, limit: int) -> dict:
    return _run([
        "financials", kind, "--thscode", thscode,
        "--period", period, "--limit", str(limit), "--format", "json",
    ])


def _latest(payload: dict, thscode: str, kind: str) -> dict:
    """Pick the most recent report; never trust upstream row order."""
    items = payload.get("item") or []
    if not items:
        raise net.FetchError(f"hithink_source_gap: no {kind} rows for {thscode}")
    return max(items, key=lambda item: (item.get("fiscal_year") or 0, item.get("period_end_ms") or 0))


def _period_label(stmt: dict) -> str:
    fiscal_year = stmt.get("fiscal_year")
    if not fiscal_year:
        return str(stmt.get("period") or "unknown")
    fiscal_period = str(stmt.get("fiscal_period") or "").strip().upper()
    if fiscal_period in ("", "FY", "ANNUAL"):
        return f"FY{fiscal_year}"
    return f"{fiscal_year}{fiscal_period}"


def _statement_date(stmt: dict) -> str:
    return _ms_to_date(stmt.get("report_date_ms") or stmt.get("period_end_ms"))


def _period_end(stmt: dict) -> str:
    return _ms_to_date(stmt.get("period_end_ms"))


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _currency_for(thscode: str) -> str:
    board = thscode.split(".")[-1]
    return _BOARD_CURRENCY.get(board, "CNY")


def _to_ms(day: _dt.date) -> int:
    """Encode a calendar date the way the vendor does: midnight in Asia/Shanghai."""
    return int(_dt.datetime(day.year, day.month, day.day, tzinfo=CN_TZ).timestamp() * 1000)


def _ms_to_date(value) -> str:
    """Decode a vendor calendar epoch (Beijing midnight) to ``YYYY-MM-DD``."""
    if value is None:
        return ""
    try:
        return _dt.datetime.fromtimestamp(int(value) / 1000, tz=CN_TZ).date().isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def _ms_to_time(value) -> str:
    """Decode a vendor instant (e.g. a snapshot timestamp) in market time."""
    if value is None:
        return ""
    try:
        return _dt.datetime.fromtimestamp(int(value) / 1000, tz=CN_TZ).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def _num(value):
    if value is None or isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
