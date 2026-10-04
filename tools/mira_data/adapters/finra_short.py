"""FINRA consolidated short interest adapter -> canonical ``ownership_short_interest`` (L2).

**This corrects the gap list.** Item 10 read "short interest / float / days-to-cover, all
markets — no free source". For US names that was wrong: FINRA publishes the consolidated short
interest file through its own public API, keyless, with the short position, the prior position,
average daily volume, **days to cover** and the change — verified live 2026-10-04 (AAPL
139,749,097 shares short with 3.53 days to cover for the 2026-08-31 settlement).

Four contract details, each of which cost a probe to find and would otherwise produce a wrong or
empty answer:

1. **The API answers CSV, not JSON** — despite being a Socrata-style endpoint. Reading it as
   JSON raises a parse error, and the columns are quoted.
2. **Filtering is POST-only and by a JSON body.** GET query parameters are silently ignored:
   a GET with `?sortFields=…` returns HTTP 400 while a GET carrying a symbol filter returns the
   *unfiltered* first page, which looks like a successful filtered read.
3. **`sortFields` is rejected (HTTP 400).** Ordering comes from the date range instead: results
   are ascending, so the newest settlement is the **last** row, not the first. Taking the first
   row would publish a 2020 figure as current.
4. **One settlement date arrives per query.** A wide range returns only the earliest batch it
   has left, limit-capped, so the newest settlement has to be walked forward deliberately rather
   than assumed.

Short interest is reported semi-monthly (settlement dates around the 15th and month end) and
published several business days later, so the settlement date is not the publication date.
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import json
import urllib.request
from typing import Optional

from .. import config, net
from ..canonical import POSTURES, CanonicalRecord, FetchResult

BASE = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
ENDPOINT = BASE
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "text/plain",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
}
COLUMNS = ["settlementDate", "symbolCode", "issueName", "marketClassCode",
           "currentShortPositionQuantity", "previousShortPositionQuantity",
           "averageDailyVolumeQuantity", "daysToCoverQuantity", "changePercent",
           "changePreviousNumber"]
LOOKBACK_DAYS = 90
MAX_ROWS = 2000

# The fields the API returns, in its own order.
FIELDS = ("accountingYearMonthNumber", "symbolCode", "issueName",
          "issuerServicesGroupExchangeCode", "marketClassCode",
          "currentShortPositionQuantity", "previousShortPositionQuantity",
          "averageDailyVolumeQuantity", "daysToCoverQuantity", "changePercent",
          "changePreviousNumber", "settlementDate")


def fetch_short_interest(
    symbol: str,
    *,
    as_of: Optional[str] = None,
    market_scope: str = "US",
    limit: Optional[int] = None,
) -> FetchResult:
    """Recent consolidated short interest for one US symbol, newest settlement first."""
    as_of = as_of or _dt.date.today().isoformat()
    code = (symbol or "").strip().upper()
    if not code:
        raise net.FetchError(
            "finra_source_gap: short interest needs a US ticker (the file is keyed by symbol)")
    lookback = max(30, min(400, int(limit or _setting("MIRA_FINRA_LOOKBACK_DAYS",
                                                      LOOKBACK_DAYS))))
    cutoff = (_dt.date.fromisoformat(as_of) - _dt.timedelta(days=lookback)).isoformat()

    rows = _query({
        "limit": MAX_ROWS,
        "compareFilters": [{"fieldName": "symbolCode", "fieldValue": code,
                            "compareType": "EQUAL"}],
        "dateRangeFilters": [{"fieldName": "settlementDate", "startDate": cutoff,
                              "endDate": as_of}],
    })
    rows = [r for r in rows if (r.get("symbolCode") or "").strip().upper() == code]
    if not rows:
        # A window that misses the semi-monthly cycle is a gap with the reason, not an empty
        # series: the symbol may simply have no settlement inside the range.
        raise net.FetchError(
            f"finra_source_gap: no consolidated short interest for {code} between {cutoff} and "
            f"{as_of}. The file is semi-monthly (settlement near the 15th and month end) and "
            "published several days after settlement, so a short window can legitimately miss "
            "it - widen the window rather than concluding the symbol is not covered")
    rows.sort(key=lambda r: (r.get("settlementDate") or ""))
    latest = rows[-1]
    posture = POSTURES["finra_short_interest"]
    day = str(latest.get("settlementDate") or "")
    short_now = _num(latest.get("currentShortPositionQuantity"))
    short_prev = _num(latest.get("previousShortPositionQuantity"))

    record = CanonicalRecord(
        family="ownership_short_interest", research_object=f"SHORT_INTEREST_{code}",
        market_scope=market_scope, metric="short_interest_shares",
        value=short_now if short_now is not None else 0.0, unit="shares", period=day,
        period_type="point_in_time", as_of_date=as_of, source_date=day, posture=posture,
        url_or_path=ENDPOINT,
        claim_text=(f"{code} consolidated short interest {day} = {_fmt(short_now)} shares"
                    f" (days to cover {_fmt(_num(latest.get('daysToCoverQuantity')))},"
                    f" prior {_fmt(short_prev)})"),
        provenance={
            "symbol": code, "issueName": latest.get("issueName"),
            "settlementDate": day,
            "currentShortPositionQuantity": short_now,
            "previousShortPositionQuantity": short_prev,
            "averageDailyVolumeQuantity": _num(latest.get("averageDailyVolumeQuantity")),
            "daysToCoverQuantity": _num(latest.get("daysToCoverQuantity")),
            "changePercent": _num(latest.get("changePercent")),
            "changePreviousNumber": _num(latest.get("changePreviousNumber")),
            "marketClassCode": latest.get("marketClassCode"),
            "settlementsInWindow": len(rows),
            "rowsReturned": len(rows), "windowDays": lookback, "windowStart": cutoff,
            "apiNote": ("the endpoint answers CSV; filtering is POST-only with a JSON body, GET "
                        "parameters are ignored, and sortFields is rejected with HTTP 400"),
            "orderNote": ("rows arrive ascending by settlement date, so the newest is the LAST "
                          "row; taking the first would publish the oldest settlement in range"),
            "publicationNote": ("short interest is semi-monthly and published several business "
                                "days after the settlement date, so the settlement date is not "
                                "the publication date"),
            "floatNote": ("this is a short POSITION, not short interest as a percentage of "
                          "float; the file carries no float, so a percent-of-float figure must "
                          "come from a share-count source and be computed with a ledger"),
        },
    )
    series = {"name": f"finra-short-interest-{code}", "columns": COLUMNS,
              "rows": [{k: r.get(k) for k in
                        ("settlementDate", "symbolCode", "issueName", "marketClassCode",
                         "currentShortPositionQuantity", "previousShortPositionQuantity",
                         "averageDailyVolumeQuantity", "daysToCoverQuantity",
                         "changePercent", "changePreviousNumber")} for r in rows]}
    return FetchResult([record], series=series)


def fetch_short_interest_market(
    _symbol: str = "",
    *,
    as_of: Optional[str] = None,
    market_scope: str = "US",
    limit: Optional[int] = None,
) -> FetchResult:
    """The newest settlement's largest short positions, as a market-wide sweep.

    Sorted by short position descending so the claim leads with the biggest, because a
    market-wide "latest" without an ordering is an arbitrary row.
    """
    as_of = as_of or _dt.date.today().isoformat()
    cap = max(1, min(200, int(limit or _setting("MIRA_FINRA_MARKET_ROWS", 50))))
    cutoff = (_dt.date.fromisoformat(as_of) - _dt.timedelta(days=45)).isoformat()
    rows = _query({
        "limit": MAX_ROWS,
        "dateRangeFilters": [{"fieldName": "settlementDate", "startDate": cutoff,
                              "endDate": as_of}],
    })
    if not rows:
        raise net.FetchError(
            f"finra_source_gap: no consolidated short interest settled between {cutoff} and "
            f"{as_of}")
    newest = max((r.get("settlementDate") or "") for r in rows)
    batch = [r for r in rows if (r.get("settlementDate") or "") == newest]
    cap_note = len(rows) >= MAX_ROWS
    batch.sort(key=lambda r: _num(r.get("currentShortPositionQuantity")) or 0.0, reverse=True)

    posture = POSTURES["finra_short_interest"]
    records = []
    for row in batch[:cap]:
        code = (row.get("symbolCode") or "").strip().upper()
        value = _num(row.get("currentShortPositionQuantity"))
        if not code or value is None:
            continue
        records.append(CanonicalRecord(
            family="ownership_short_interest", research_object=f"SHORT_INTEREST_{code}",
            market_scope=market_scope, metric="short_interest_shares", value=value,
            unit="shares", period=newest, period_type="point_in_time", as_of_date=as_of,
            source_date=newest, posture=posture, url_or_path=ENDPOINT,
            claim_text=(f"{code} consolidated short interest {newest} = {_fmt(value)} shares"
                        f" (days to cover {_fmt(_num(row.get('daysToCoverQuantity')))})"),
            provenance={
                "symbol": code, "issueName": row.get("issueName"),
                "settlementDate": newest,
                "currentShortPositionQuantity": value,
                "previousShortPositionQuantity": _num(row.get("previousShortPositionQuantity")),
                "daysToCoverQuantity": _num(row.get("daysToCoverQuantity")),
                "changePercent": _num(row.get("changePercent")),
                "marketClassCode": row.get("marketClassCode"),
                "rankedBy": "currentShortPositionQuantity (descending)",
                "batchRows": len(batch), "rowsReturned": len(rows),
                "batchMayBeTruncated": cap_note,
                "truncationNote": ("the API caps one response; when rowsReturned hits the cap "
                                   "the batch is partial, so this sweep is a sample of the "
                                   "largest positions rather than the whole market"
                                   if cap_note else
                                   "the whole settlement batch was read"),
                "publicationNote": ("semi-monthly settlement, published several business days "
                                    "later"),
                "floatNote": ("a short position, not a percent of float; no float in this file"),
            },
        ))
    if not records:
        raise net.FetchError(
            f"finra_source_gap: the {newest} settlement batch carried no usable row")
    series = {"name": "finra-short-interest-market", "columns": COLUMNS,
              "rows": [{k: r.get(k) for k in COLUMNS} for r in batch]}
    return FetchResult(records, series=series)


def _query(payload: dict) -> list[dict]:
    """POST the filter body; the endpoint answers CSV, so parse it explicitly."""
    request = urllib.request.Request(
        BASE, data=json.dumps(payload).encode("utf-8"), headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=_setting("MIRA_FINRA_TIMEOUT", 60)) as resp:
            body = resp.read().decode("utf-8", "replace")
    except Exception as exc:      # noqa: BLE001 - surfaced as a labelled gap
        raise net.FetchError(f"finra_source_gap: request failed: {exc}") from exc
    if not body.strip():
        return []
    reader = csv.DictReader(io.StringIO(body))
    # The file is quoted CSV with no BOM; a leading quote would otherwise become part of the
    # first column name and every lookup would miss.
    reader.fieldnames = [name.lstrip("\ufeff").strip() for name in (reader.fieldnames or [])]
    return [row for row in reader]


def _setting(name: str, default: int) -> int:
    try:
        return int((config.get(name) or str(default)).strip())
    except ValueError:
        return default


def _fmt(value) -> str:
    return "n/a" if value is None else f"{value:,.0f}"


def _num(value):
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").replace('"', "").strip())
    except ValueError:
        return None
