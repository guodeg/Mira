"""A-share ETF options surface via the hithink-finance CLI -> ``options_surface``.

Closes the last family with no working adapter: ``options_surface`` previously only had
the Futu path, whose optional dependency is not installed, so an A-share option surface
was unreachable. ETF options are the liquid onshore listed-option market (50ETF, 300ETF,
500ETF, 科创50ETF, 创业板ETF), and the licensed CLI already carries them.

Contract shape (probed live 2026-10-02)
--------------------------------------
- ``options contracts --limit N`` lists contracts **grouped by underlying, nearest expiry
  first** (the first 100 rows covered exactly 510050/510300/510500/588000/588080). Each
  row carries ``ticker`` (``510050C2612M02800`` — underlying, C/P, YYMM, M/A, strike),
  ``name`` (``50ETF购12月2800``), ``thscode`` and the listing/expiry/delivery dates.
  There is no per-underlying filter, so the surface is selected client-side from the
  scanned rows and the scan is bounded.
- ``options contract-detail --thscode`` supplies the fields the list lacks:
  ``strike_price``, ``option_type`` (call/put), ``exercise_style`` (european),
  ``underlying_code``, ``margin_rate``, ``trade_amount`` (contract size).
- ``options daily --thscode`` supplies OHLC/volume/turnover per day. **No open interest
  and no implied volatility are published through this endpoint**, so the surface is
  priced but cannot report OI, IV rank or skew — that is a data limit, not a bug, and it
  is recorded in every record's provenance rather than left for a reader to assume.

Why the strike comes from ``contract-detail`` and not from parsing ``ticker``: the ticker
does encode the strike, but a parse of a vendor naming convention is exactly the kind of
silent assumption this codebase avoids. One authoritative call per surfaced contract
costs more requests and is worth it; the ticker is still recorded verbatim in provenance.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional

from .. import config, net
from ..canonical import POSTURES, CanonicalRecord, FetchResult
from .hithink_finance import CN_TZ, _run as hithink_run, resolve_thscode

ENDPOINT = "hithink-finance://options.contract-detail/{thscode}"
NOT_PUBLISHED = "open_interest,implied_volatility"


def _setting(name: str, default: str) -> str:
    return (config.get(name) or default).strip()


def _max_contracts(limit: Optional[int]) -> int:
    if limit is not None:
        return max(1, int(limit))
    try:
        return max(1, int(_setting("MIRA_OPTIONS_MAX_CONTRACTS", "10")))
    except ValueError:
        return 10


def _scan_rows() -> int:
    try:
        return max(20, min(1000, int(_setting("MIRA_OPTIONS_SCAN_ROWS", "200"))))
    except ValueError:
        return 200


def _quote_days() -> int:
    try:
        return max(5, int(_setting("MIRA_OPTIONS_QUOTE_DAYS", "20")))
    except ValueError:
        return 20


def fetch_option_surface(
    underlying: str,
    *,
    as_of: Optional[str] = None,
    market_scope: str = "CN",
    expiry: Optional[str] = None,
    max_contracts: Optional[int] = None,
) -> FetchResult:
    """Priced option contracts for one ETF underlying, nearest expiry first."""
    as_of = as_of or _dt.date.today().isoformat()
    thscode = resolve_thscode(underlying)
    code, board = thscode.split(".")
    if board == "BJ":
        raise net.FetchError(
            f"options_source_gap: no listed options for Beijing-exchange names ({thscode})")

    listed = _list_contracts(code)
    if not listed:
        raise net.FetchError(
            f"options_source_gap: no listed option contracts for {thscode} in the first "
            f"{_scan_rows()} rows (ETF options exist for 510050/510300/510500/588000/"
            "588080 and similar underlyings)")
    expiries = sorted({item["_expiry"] for item in listed if item["_expiry"]})
    chosen = _choose_expiry(expiries, expiry, as_of)
    selected = [item for item in listed if item["_expiry"] == chosen]
    selected.sort(key=lambda item: item["ticker"])
    selected = selected[:_max_contracts(max_contracts)]

    records: list[CanonicalRecord] = []
    gaps: list[str] = []
    for item in selected:
        try:
            records.append(_priced_record(item, thscode=thscode, market_scope=market_scope,
                                          as_of=as_of, expiry=chosen))
        except net.FetchError as exc:
            gaps.append(f"{item['ticker']}: {exc}")
    if not records:
        raise net.FetchError(
            f"options_source_gap: no priced contract for {thscode} expiry {chosen}"
            + (f" ({'; '.join(gaps[:2])})" if gaps else ""))
    return FetchResult(records)


def _list_contracts(code: str) -> list[dict]:
    payload = hithink_run([
        "options", "contracts", "--limit", str(_scan_rows()), "--offset", "0",
        "--format", "json",
    ])
    rows = payload.get("item") or []
    out = []
    for row in rows:
        ticker = str(row.get("ticker") or "")
        if not ticker.startswith(code):
            continue        # rows are grouped by underlying; keep only this one
        expiry = _expiry_from_ticker(ticker)
        out.append({**row, "_expiry": expiry})
    return out


def _expiry_from_ticker(ticker: str) -> str:
    """``510050C2612M02800`` -> ``2026-12`` (grouping only; the label comes from detail)."""
    tail = ticker[len(ticker[:6]):]
    digits = "".join(ch for ch in tail[1:] if ch.isdigit())[:4]
    if len(digits) != 4:
        return ""
    return f"20{digits[:2]}-{digits[2:]}"


def _choose_expiry(expiries: list[str], requested: Optional[str], as_of: str) -> str:
    if requested:
        wanted = _normalise_expiry(requested)
        if wanted not in expiries:
            raise net.FetchError(
                f"options_source_gap: expiry {requested!r} is not listed for this underlying; "
                f"available: {', '.join(expiries)}")
        return wanted
    forward = [item for item in expiries if item >= as_of[:7]]
    return forward[0] if forward else expiries[-1]


def _normalise_expiry(value: str) -> str:
    text = value.strip().replace("/", "-")
    if len(text) == 6 and text.isdigit():          # 202612
        return f"{text[:4]}-{text[4:]}"
    if len(text) == 7 and text[4] == "-":
        return text
    if len(text) == 4 and text.isdigit():          # 2612
        return f"20{text[:2]}-{text[2:]}"
    raise net.FetchError(f"invalid_expiry: {value!r} (use YYYY-MM)")


def _priced_record(item: dict, *, thscode: str, market_scope: str, as_of: str,
                   expiry: str) -> CanonicalRecord:
    contract = str(item.get("thscode") or "")
    detail = hithink_run(["options", "contract-detail", "--thscode", contract,
                          "--format", "json"])
    bars = hithink_run([
        "options", "daily", "--thscode", contract,
        "--start", str(_ms(as_of, -_quote_days())), "--end", str(_ms(as_of, 1)),
        "--format", "json",
    ]).get("item") or []
    if not bars:
        raise net.FetchError("no daily bars")
    latest = max(bars, key=lambda bar: bar.get("timestamp") or 0)
    close = latest.get("close_price")
    if close is None:
        raise net.FetchError("latest bar has no close")

    quote_date = _bj_date(latest.get("timestamp"))
    last_trade = str(detail.get("last_trade_date") or item.get("last_trade_date") or "")
    posture = POSTURES["hithink_finance_options"]
    strike = detail.get("strike_price")
    option_type = detail.get("option_type")
    days_to_expiry = _days_between(quote_date, last_trade)
    return CanonicalRecord(
        family="options_surface", research_object=thscode, market_scope=market_scope,
        metric="option_last_close", value=close, unit="CNY", currency="CNY",
        period=quote_date, period_type="point_in_time", as_of_date=as_of,
        source_date=quote_date, posture=posture, url_or_path=ENDPOINT.format(thscode=thscode),
        claim_text=(f"{thscode} {item.get('name')} ({option_type}, strike {strike}, "
                    f"到期 {last_trade}) last_close = {close} CNY ({quote_date})"),
        provenance={
            "contractCode": contract, "ticker": item.get("ticker"), "contractName": item.get("name"),
            "optionType": option_type, "strikePrice": strike,
            "expiry": last_trade, "expiryMonth": expiry,
            "exerciseStyle": detail.get("exercise_style"),
            "contractSize": detail.get("trade_amount"), "marginRate": detail.get("margin_rate"),
            "underlyingCode": detail.get("underlying_code"),
            "quoteDate": quote_date, "volume": latest.get("volume"),
            "turnover": latest.get("turnover"), "daysToExpiry": days_to_expiry,
            "notPublished": NOT_PUBLISHED,
        },
    )


def _ms(as_of: str, day_offset: int) -> int:
    day = _dt.date.fromisoformat(as_of) + _dt.timedelta(days=day_offset)
    return int(_dt.datetime(day.year, day.month, day.day, tzinfo=CN_TZ).timestamp() * 1000)


def _bj_date(epoch_ms) -> str:
    if epoch_ms is None:
        return ""
    try:
        return _dt.datetime.fromtimestamp(int(epoch_ms) / 1000, tz=CN_TZ).date().isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def _days_between(start: str, end: str) -> Optional[int]:
    try:
        return (_dt.date.fromisoformat(end) - _dt.date.fromisoformat(start)).days
    except (TypeError, ValueError):
        return None
