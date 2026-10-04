"""Futures warehouse receipts (仓单) and basis, aggregated relays.

**Why this is an L5 channel and not L2, stated plainly.** The exchange stock files that would
make this primary are not reachable: SHFE's `dailystock` file was probed on both its hosts and
several path shapes and is not there (its working daily JSON carries settlement prices but no
warehouse or spot fields), CZCE publishes receipts only as a per-session PDF whose URL is an
opaque rootfiles hash that cannot be derived, and DCE refuses a scripted client outright. The
reference libraries reach neither: akshare's inventory functions read 99qh and Eastmoney, not
the exchanges. So the honest tier for both families here is L5 aggregator, and each record says
so rather than implying exchange provenance.

What the relays do provide is usable and, for basis, checkable:

- **warehouse receipts** (Eastmoney): the registered warrant quantity per variety per session
  plus its day change. The vendor's own column is ``ON_WARRANT_NUM`` (注册仓单量).
- **basis** (licensed vendor CLI): spot price, futures close and settlement, and the four
  derived basis figures the vendor publishes, with the reference delivery site and the spot
  publication date. Measured live 2026-10-03 on 144 rows; the identity
  ``close_basis = spot - close`` holds (rb: 3260 - 3112 = 148), which is the cross-check that
  makes the relayed number usable.

The derived basis figures are **vendor-published, not recomputed here**, so they carry no
calculation-ledger obligation; provenance records that they were published rather than derived
by Mira.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
import urllib.parse
from typing import Optional

from .. import config, net
from ..canonical import POSTURES, CanonicalRecord, FetchResult

EM_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
EM_REPORT = "RPT_FUTU_STOCKDATA"
EM_ENDPOINT = "eastmoney://futu-stock/{variety}"
EM_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Referer": "https://data.eastmoney.com/ifdata/kcsj.html",
}
INVENTORY_COLUMNS = ["variety", "date", "warrant_num", "change", "unit"]
EM_DEFAULT_DAYS = 10

BASIS_ENDPOINT = "hithink-finance://futures/latest-basis"
BASIS_COLUMNS = ["thscode", "variety_name", "reference_site", "spot_publish_date",
                 "spot_price", "converted_spot_price", "close_price", "settle_price",
                 "close_basis", "settle_basis", "close_basis_rate", "settle_basis_rate"]
BASIS_NOT_RECOMPUTED = (
    "the basis figures are the vendor's own published values, not recomputed by Mira, so they "
    "are reported metrics rather than derived calculations")


# --------------------------------------------------------------------------- #
# Warehouse receipts (Eastmoney relay)
# --------------------------------------------------------------------------- #

def fetch_warehouse_receipts(
    variety: str,
    *,
    days: Optional[int] = None,
    as_of: Optional[str] = None,
    market_scope: str = "CN",
    max_items: Optional[int] = None,
) -> FetchResult:
    """Registered warrant quantity per session for one variety, newest first.

    The variety is a futures product code (``ps``, ``rb``, ``cu``); the relay stores it in
    ``SECURITY_CODE``. A missing variety or an empty window is a labelled gap.
    """
    as_of = as_of or _dt.date.today().isoformat()
    window = max(1, min(120, int(days or _setting("MIRA_FUTURES_INVENTORY_DAYS",
                                                  EM_DEFAULT_DAYS))))
    limit = max(1, min(500, int(max_items or _setting("MIRA_FUTURES_INVENTORY_MAX",
                                                      60))))
    # The relay's variety casing is INCONSISTENT: the same result set contains `RB` and `CU`
    # alongside `si` and `lc`, and an unmatched case returns an empty result rather than an
    # error - which reads exactly like "this variety has no receipts". So both forms are tried
    # before concluding there is no data.
    wanted = variety.strip().upper()
    rows = []
    for candidate in (wanted, wanted.lower()):
        params = {
            "reportName": EM_REPORT, "columns": "ALL", "pageNumber": "1",
            "pageSize": str(limit), "sortColumns": "TRADE_DATE", "sortTypes": "-1",
            "source": "WEB", "client": "WEB",
            "filter": f'(SECURITY_CODE="{candidate}")',
        }
        payload = net.get_json(EM_URL + "?" + urllib.parse.urlencode(params),
                               headers=EM_HEADERS, retries=2, backoff=1.5)
        rows = ((payload.get("result") or {}).get("data") or []) if isinstance(payload, dict) else []
        if rows:
            break
    if not rows:
        raise net.FetchError(
            f"futures_inventory_source_gap: the relay reports no warehouse receipt rows for "
            f"{wanted!r} in either case form. The relay's code list is fetchable but its casing "
            "is inconsistent; a variety it does not carry cannot be distinguished from a "
            "misspelled one here")

    cutoff = (_dt.date.fromisoformat(as_of) - _dt.timedelta(days=window)).isoformat()
    posture = POSTURES["em_futures_inventory"]
    series_rows, records = [], []
    for row in rows:
        day = _day(row.get("TRADE_DATE"))
        if not day or day < cutoff:
            continue
        num = _num(row.get("ON_WARRANT_NUM"))
        series_rows.append({
            "variety": wanted, "date": day, "warrant_num": num,
            "change": _num(row.get("ADDCHANGE")), "unit": row.get("UNIT"),
        })
    if not series_rows:
        raise net.FetchError(
            f"futures_inventory_source_gap: {wanted} has receipt rows but none inside the last "
            f"{window} days up to {as_of}")

    latest = series_rows[0]
    records.append(CanonicalRecord(
        family="macro_series", research_object=f"{wanted.upper()}_WAREHOUSE",
        market_scope=market_scope, metric=f"{wanted}_warehouse_receipt",
        value=float(latest["warrant_num"]) if latest["warrant_num"] is not None else 0.0,
        unit="warrants", period=latest["date"], period_type="point_in_time",
        as_of_date=as_of, source_date=latest["date"], posture=posture,
        url_or_path=EM_ENDPOINT.format(variety=wanted),
        claim_text=(f"{wanted.upper()} 注册仓单 {latest['date']} "
                    f"{latest['warrant_num']:,.0f} 手"
                    + (f"（较前一交易日 {latest['change']:+,.0f}）"
                       if latest["change"] is not None else "")),
        provenance={
            "variety": wanted, "tradeDate": latest["date"],
            "warrantNum": latest["warrant_num"], "change": latest["change"],
            "vendorField": "ON_WARRANT_NUM (注册仓单量)",
            "tierBasis": TIER_NOTE,
            "reportName": EM_REPORT, "windowDays": window, "rowsReturned": len(rows),
            "rowsInWindow": len(series_rows),
        },
    ))
    series = {"name": f"warehouse_receipts-{wanted}", "columns": INVENTORY_COLUMNS,
              "rows": series_rows}
    return FetchResult(records, series=series)


TIER_NOTE = (
    "L5 aggregator, not the exchange: the official stock files are not reachable (SHFE's "
    "dailystock is absent from both its hosts, CZCE publishes receipts only as an opaque "
    "per-session PDF, DCE refuses a scripted client), so this quantity is a relayed figure and "
    "must be cross-checked against the exchange's own daily bulletin before anchoring a "
    "durable conclusion")


# --------------------------------------------------------------------------- #
# Basis (licensed vendor CLI)
# --------------------------------------------------------------------------- #

def fetch_basis(
    variety: Optional[str] = None,
    *,
    as_of: Optional[str] = None,
    market_scope: str = "CN",
    max_items: Optional[int] = None,
) -> FetchResult:
    """Latest main-continuous basis per variety, from the licensed vendor CLI.

    ``variety`` filters by variety name or ticker substring (``rb``, ``螺纹``); ``None`` keeps
    every variety the vendor publishes (144 rows when measured). The vendor computes the basis
    figures; this adapter reports them verbatim and records that it did not recompute them.
    """
    as_of = as_of or _dt.date.today().isoformat()
    payload = _vendor_json(["futures", "latest-basis"])
    data = payload.get("data")
    items = (data.get("item") or data.get("items") or []) if isinstance(data, dict) else (data or [])
    if not items:
        raise net.FetchError("futures_basis_source_gap: the vendor returned no basis rows")

    limit = max(1, min(300, int(max_items or _setting("MIRA_FUTURES_BASIS_MAX", 40))))
    needle = (variety or "").strip().lower()
    posture = POSTURES["hithink_futures_basis"]
    records, series_rows = [], []
    for item in items:
        ticker = str(item.get("ticker") or "").strip()
        name = str(item.get("variety_name") or "").strip()
        if needle and needle not in ticker.lower() and needle not in name.lower():
            continue
        series_rows.append({k: item.get(k) for k in BASIS_COLUMNS})
        if len(records) >= limit:
            continue
        spot = _num(item.get("spot_price"))
        close = _num(item.get("close_price"))
        basis = _num(item.get("close_basis"))
        # The identity the vendor's own numbers should satisfy; a mismatch is reported, not
        # silently published as if the relay were internally consistent.
        consistent = (None if None in (spot, close, basis)
                      else abs((spot - close) - basis) < 0.51)
        records.append(CanonicalRecord(
            family="macro_series", research_object=str(item.get("thscode") or ticker),
            market_scope=market_scope, metric=f"{ticker or name}_close_basis",
            value=basis if basis is not None else 0.0, unit="price",
            period=_day(item.get("spot_publish_date")) or as_of, period_type="point_in_time",
            as_of_date=as_of, source_date=_day(item.get("spot_publish_date")) or as_of,
            posture=posture, url_or_path=BASIS_ENDPOINT,
            claim_text=(f"{name or ticker} 现货 {_fmt(spot)} − 期货收盘 {_fmt(close)} = "
                        f"基差 {_fmt(basis)}（基差率 {item.get('close_basis_rate')}%）"),
            provenance={
                "thscode": item.get("thscode"), "ticker": ticker, "varietyName": name,
                "referenceSite": item.get("reference_site"),
                "spotIndicatorId": item.get("spot_indicator_id"),
                "spotPublishDate": item.get("spot_publish_date"),
                "spotPrice": spot, "convertedSpotPrice": _num(item.get("converted_spot_price")),
                "closePrice": close, "settlePrice": _num(item.get("settle_price")),
                "closeBasis": basis, "settleBasis": _num(item.get("settle_basis")),
                "closeBasisRate": _num(item.get("close_basis_rate")),
                "settleBasisRate": _num(item.get("settle_basis_rate")),
                "averageCloseBasis": _num(item.get("average_close_basis")),
                "vendorUpdatedAt": item.get("updated_at"),
                "identityCheck": ("the vendor's own spot - close - basis reconciles"
                                  if consistent else
                                  "the vendor's spot, close and basis do NOT reconcile"),
                "identityConsistent": consistent,
                "notRecomputed": BASIS_NOT_RECOMPUTED,
                "tierBasis": TIER_NOTE,
                "spotDateNote": ("the spot publication date can differ from the futures session, "
                                 "so it travels with the basis"),
            },
        ))
    if not records:
        detail = f" matching {variety!r}" if needle else ""
        raise net.FetchError(
            f"futures_basis_source_gap: no basis row{detail}; the vendor publishes "
            f"{len(items)} varieties")
    series = {"name": "futures-basis", "columns": BASIS_COLUMNS, "rows": series_rows}
    return FetchResult(records, series=series)


def _vendor_json(argv: list[str]) -> dict:
    """Run one vendor-CLI command and read its JSON envelope.

    Reuses the hithink adapter's binary resolution and ``.cmd``-shim handling rather than
    re-deriving them, and asks the CLI to write a file with ``--output`` instead of capturing
    stdout: an npm shim's output is not reliably capturable through a pipe, and the CLI
    documents ``--output`` for exactly this.
    """
    from . import hithink_finance as hf

    binary = hf.resolve_bin()
    out = os.path.join(os.environ.get("TEMP") or "/tmp", "mira-vendor-envelope.json")
    full = [*argv, "--format", "json", "--output", out]
    try:
        subprocess.run(hf._argv(binary, full), check=True, timeout=hf._timeout(),
                       capture_output=True)
    except FileNotFoundError as exc:
        raise net.FetchError(f"hithink_cli_gap: could not execute {binary}") from exc
    except subprocess.TimeoutExpired as exc:
        raise net.FetchError(f"futures_basis_source_gap: {binary} timed out") from exc
    except subprocess.CalledProcessError as exc:
        raise net.FetchError(
            f"futures_basis_source_gap: {binary} exited {exc.returncode}") from exc
    try:
        with open(out, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise net.FetchError(f"futures_basis_source_gap: unreadable CLI output: {exc}") from exc


def _setting(name: str, default: int) -> int:
    try:
        return int((config.get(name) or str(default)).strip())
    except ValueError:
        return default


def _num(value):
    if value is None or value == "":
        return None
    try:
        return float(re.sub(r"[,%\s]", "", str(value)))
    except ValueError:
        return None


def _fmt(value) -> str:
    return "n/a" if value is None else f"{value:,.2f}".rstrip("0").rstrip(".")


def _day(value) -> str:
    match = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", str(value or ""))
    return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}" if match else ""
