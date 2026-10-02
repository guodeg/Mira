"""Eastmoney profit-forecast table adapter -> canonical ``consensus_estimate``.

Fills the A-share expectation baseline: consensus EPS by forecast year, the
six-month sell-side rating distribution, and the target-price range. Keyless
public endpoint; the reference implementation is
``akshare.stock_profit_forecast_em`` (MIT), re-implemented here in stdlib because
Mira's core stays dependency-free.

Evidence tier: L5 ``consensus_and_estimates`` / ``forecast`` / ``estimate``.
Everything this endpoint returns is a *sell-side expectation*, not an issuer fact:
per ``data/claim-taxonomy.md`` a forecast "只代表预期，不代表事实", so these claims
describe what analysts expect and must never anchor a reported-metric conclusion.

Contract (probed live 2026-10-02)
--------------------------------
``GET https://datacenter-web.eastmoney.com/api/data/v1/get`` with
``reportName=RPT_WEB_RESPREDICT``, ``columns=ALL``, ``pageNumber/pageSize``,
``filter=(SECURITY_CODE="600519")``, ``source=WEB``, ``client=WEB``. The filter is
applied server-side, so a single-name read returns exactly one row holding all four
fiscal slots: ``YEAR1..YEAR4`` + ``YEAR_MARK1..4`` + ``EPS1..4``.

Verified field semantics
------------------------
- ``YEAR_MARKn`` is ``"A"`` for the actual (already reported) year and ``"E"`` for an
  estimate. Only ``E`` slots become claims here; the ``A`` slot is carried in
  provenance as the forecast's base. (The akshare reference labels all four years
  "预测每股收益", which mislabels the actual year.)
- ``RATING_ORG_NUM`` is the report count and ``RATING_*_NUM`` the buy/add/neutral/
  reduce/sell tallies; the vendor labels this block 机构投资评级(近六个月), i.e. a
  **six-month** window, which the metric names below say explicitly.
- ``DEC_AIMPRICEMAX/MIN`` are the highest and lowest analyst target prices.

Vintage caveat
--------------
The payload carries **no as-of timestamp**: the vendor does not say when the
consensus was last updated. The retrieval date is therefore recorded as
``source_date`` and every record states that the vintage is retrieval-time only, so
a downstream reader cannot mistake it for a dated consensus vintage. Point-in-time
revision history does not exist upstream — building it means snapshotting this
endpoint over time.
"""

from __future__ import annotations

import datetime as _dt
import urllib.parse
from typing import Optional

from .. import net
from ..canonical import POSTURES, CanonicalRecord, FetchResult
from .hithink_finance import resolve_thscode  # shared A-share symbol -> thscode map

API_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REPORT_NAME = "RPT_WEB_RESPREDICT"
ENDPOINT = "eastmoney://RPT_WEB_RESPREDICT/{thscode}"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Referer": "https://data.eastmoney.com/",
}

# (metric, vendor field) for the six-month rating block; nulls are skipped, never zeroed.
RATING_FIELDS = [
    ("rating_report_count_6m", "RATING_ORG_NUM"),
    ("rating_buy_count_6m", "RATING_BUY_NUM"),
    ("rating_add_count_6m", "RATING_ADD_NUM"),
    ("rating_neutral_count_6m", "RATING_NEUTRAL_NUM"),
    ("rating_reduce_count_6m", "RATING_REDUCE_NUM"),
    ("rating_sale_count_6m", "RATING_SALE_NUM"),
]
TARGET_PRICE_FIELDS = [
    ("target_price_high", "DEC_AIMPRICEMAX"),
    ("target_price_low", "DEC_AIMPRICEMIN"),
]
VINTAGE_CAVEAT = "vendor payload has no as-of timestamp; vintage = retrieval date"


def _query(code: str) -> dict:
    params = {
        "reportName": REPORT_NAME,
        "columns": "ALL",
        "pageNumber": "1",
        "pageSize": "5",
        "sortColumns": "RATING_ORG_NUM",
        "sortTypes": "-1",
        "filter": f'(SECURITY_CODE="{code}")',
        "source": "WEB",
        "client": "WEB",
    }
    url = API_URL + "?" + urllib.parse.urlencode(params)
    payload = net.get_json(url, headers=HEADERS, retries=2, backoff=1.5)
    result = payload.get("result") or {}
    rows = result.get("data") or []
    if not payload.get("success"):
        message = payload.get("message") or payload.get("code")
        if not rows:
            # The vendor signals "no coverage" as success=false with 返回数据为空
            # rather than an empty list: that is nothing to read, not a failure.
            raise net.FetchError(
                f"eastmoney_source_gap: no profit-forecast row for {code} ({message})")
        raise net.FetchError(f"eastmoney_error: {message} for {code}")
    if not rows:
        raise net.FetchError(
            f"eastmoney_source_gap: no profit-forecast row for {code} "
            "(no sell-side coverage, or an unsupported security)")
    return rows[0]


def fetch_consensus(
    symbol: str,
    *,
    as_of: Optional[str] = None,
    market_scope: str = "CN",
) -> FetchResult:
    """Consensus estimates for one A-share name (single-name on-demand read only)."""
    as_of = as_of or _dt.date.today().isoformat()
    thscode = resolve_thscode(symbol)
    code = thscode.split(".")[0]
    row = _query(code)

    posture = POSTURES["eastmoney_consensus"]
    url = ENDPOINT.format(thscode=thscode)
    actual_year, actual_eps = _actual_slot(row)
    base_provenance = {
        "secuCode": row.get("SECUCODE"),
        "securityName": row.get("SECURITY_NAME_ABBR"),
        "sourceTable": REPORT_NAME,
        "industryBoard": row.get("INDUSTRY_BOARD"),
        "ratingWindow": "6m",
        "vintage": VINTAGE_CAVEAT,
    }
    if actual_year:
        base_provenance["forecastBase"] = f"FY{actual_year}A={actual_eps}"

    def record(metric, value, unit, period, period_type, extra=None):
        provenance = dict(base_provenance)
        if extra:
            provenance.update(extra)
        return CanonicalRecord(
            family="consensus_estimate", research_object=thscode, market_scope=market_scope,
            metric=metric, value=value, unit=unit,
            currency="CNY" if unit in ("CNY", "CNY/share") else None,
            period=period, period_type=period_type, as_of_date=as_of, source_date=as_of,
            posture=posture, url_or_path=url, provenance=provenance,
        )

    records: list[CanonicalRecord] = []
    for slot in (1, 2, 3, 4):
        year, mark, eps = row.get(f"YEAR{slot}"), row.get(f"YEAR_MARK{slot}"), row.get(f"EPS{slot}")
        if mark != "E" or eps is None or year is None:
            continue          # the "A" slot is the reported base, not an estimate
        records.append(record("consensus_eps", _num(eps), "CNY/share",
                              f"FY{year}E", "forward",
                              {"forecastYear": year, "yearMark": mark}))

    for metric, field in TARGET_PRICE_FIELDS:
        value = _num(row.get(field))
        if value is not None:
            records.append(record(metric, value, "CNY", as_of, "point_in_time",
                                  {"vendorField": field}))
    for metric, field in RATING_FIELDS:
        value = _num(row.get(field))
        if value is not None:                       # null must not become 0
            records.append(record(metric, value, "count", as_of, "point_in_time",
                                  {"vendorField": field}))

    if not records:
        raise net.FetchError(
            f"eastmoney_source_gap: row for {code} carried no usable consensus fields")
    order = {"consensus_eps": 0}
    records.sort(key=lambda r: (order.get(r.metric, 1), r.period))
    return FetchResult(records)


def _actual_slot(row: dict) -> tuple[Optional[int], object]:
    """The ``A``-marked slot, used as the forecast's reported base."""
    for slot in (1, 2, 3, 4):
        if row.get(f"YEAR_MARK{slot}") == "A" and row.get(f"YEAR{slot}") is not None:
            return row.get(f"YEAR{slot}"), row.get(f"EPS{slot}")
    return None, None


def _num(value):
    if value is None or isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
