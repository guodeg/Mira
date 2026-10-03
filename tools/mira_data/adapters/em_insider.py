"""董监高持股变动 (executive shareholding changes): the A-share counterpart of a Form 4 feed.

Read through the Eastmoney datacenter relay, so the tier is **L5**: this is an aggregator's
structured copy, not the issuer's or the exchange's own publication. The authoritative route -
the exchange and CNINFO filings behind each change - is the L2 upgrade path and is recorded as
such in every record rather than implied.

Verified live (2026-10-03)::

    GET https://datacenter-web.eastmoney.com/api/data/v1/get
        reportName=RPT_EXECUTIVE_HOLD_DETAILS & columns=ALL & pageNumber=1 & pageSize=N
        sortColumns=CHANGE_DATE & sortTypes=-1 & source=WEB & client=WEB
        filter=(SECURITY_CODE="600519")      -> {"result": {"count": 2, "data": [...]}}

Two traps, both probed rather than assumed:

* the filter takes the **bare six-digit code**; ``filter=(DERIVE_SECURITY_CODE="600519")``
  answers ``返回数据为空`` even though every row *carries* a suffixed ``DERIVE_SECURITY_CODE``
  (``600022.SH``) next to the bare ``SECURITY_CODE`` (``600022``);
* ``RPT_EXECUTIVE_HOLD`` without the ``_DETAILS`` suffix does not exist
  (``报表配置不存在``), and neither do ``RPT_SHAREHOLDER_HOLD`` / ``RPT_HOLDERCHANGE`` - the
  major-shareholder table is a different report that has not been located yet.

Field semantics taken from live rows: ``CHANGE_SHARES`` is **signed** (``-555194`` is a sale,
``40000`` a purchase), ``CHANGE_DATE`` is a datetime string whose day is used, and
``BEGIN_HOLD_NUM`` / ``END_HOLD_NUM`` are frequently ``None`` - a missing holding is skipped,
never zeroed.
"""

from __future__ import annotations

import datetime as _dt
import re
import urllib.parse
from typing import Optional

from .. import config, net
from ..canonical import POSTURES, CanonicalRecord, FetchResult
from .hithink_finance import resolve_thscode

URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REPORT = "RPT_EXECUTIVE_HOLD_DETAILS"
ENDPOINT = "eastmoney://executive-hold-details/{symbol}"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Referer": "https://data.eastmoney.com/",
}
DEFAULT_MAX_EVENTS = 20
L2_UPGRADE_NOTE = ("each change is also filed by the issuer and hosted by the exchange/CNINFO; "
                   "this row is the aggregator's structured copy (L5), not that filing")
# metric -> (vendor field, unit, currency)
METRICS = (
    ("insider_change_shares", "CHANGE_SHARES", "shares", None),
    ("insider_change_amount", "CHANGE_AMOUNT", "CNY", "CNY"),
    ("insider_avg_price", "AVERAGE_PRICE", "CNY_per_share", "CNY"),
    ("insider_hold_after", "END_HOLD_NUM", "shares", None),
)


def _max_events(limit: Optional[int]) -> int:
    if limit is not None:
        return max(1, min(200, int(limit)))
    try:
        return max(1, min(200, int((config.get("MIRA_EM_INSIDER_MAX_EVENTS")
                                    or str(DEFAULT_MAX_EVENTS)).strip())))
    except ValueError:
        return DEFAULT_MAX_EVENTS


def fetch_executive_holdings(
    symbol: str,
    *,
    since: Optional[str] = None,
    until: Optional[str] = None,
    max_items: Optional[int] = None,
    as_of: Optional[str] = None,
    market_scope: str = "CN",
) -> FetchResult:
    """One record per reported metric of each executive shareholding change for one name."""
    as_of = as_of or _dt.date.today().isoformat()
    thscode = resolve_thscode(symbol)
    code, _board = thscode.split(".")
    until = until or as_of
    since = since or (_dt.date.fromisoformat(until) - _dt.timedelta(days=1095)).isoformat()
    limit = _max_events(max_items)

    params = {
        "reportName": REPORT, "columns": "ALL", "pageNumber": "1", "pageSize": str(limit),
        "sortColumns": "CHANGE_DATE", "sortTypes": "-1", "source": "WEB", "client": "WEB",
        "filter": f'(SECURITY_CODE="{code}")',
    }
    payload = net.get_json(URL + "?" + urllib.parse.urlencode(params), headers=HEADERS,
                           retries=2, backoff=1.5)
    result = payload.get("result") if isinstance(payload, dict) else None
    rows = (result or {}).get("data") or []
    if not rows:
        raise net.FetchError(
            f"eastmoney_source_gap: no executive shareholding change reported for {thscode} "
            f"between {since} and {until}")

    posture = POSTURES["em_executive_holdings"]
    records: list[CanonicalRecord] = []
    for row in rows:
        day = _day(row.get("CHANGE_DATE"))
        if not day or day < since or day > until:
            continue
        change_id = str(row.get("GGEID") or f"{code}|{day}|{row.get('DSE_PERSON_NAME')}")
        person = _clean(row.get("DSE_PERSON_NAME"))
        shares = _number(row.get("CHANGE_SHARES"))
        if shares is None:
            continue
        direction = "减持" if shares < 0 else "增持"
        for metric, field, unit, currency in METRICS:
            value = _number(row.get(field))
            if value is None:
                continue
            records.append(CanonicalRecord(
                family="ownership_short_interest", research_object=thscode,
                market_scope=market_scope, metric=metric, value=value, unit=unit,
                currency=currency, period=day, period_type="point_in_time",
                as_of_date=as_of, source_date=day, posture=posture,
                url_or_path=ENDPOINT.format(symbol=thscode),
                claim_text=(f"{thscode} {day} {person} {direction} {abs(shares):,.0f} 股"
                            f"（{metric}）"),
                provenance={
                    "person": person, "changeReason": _clean(row.get("CHANGE_REASON")),
                    "holdType": _clean(row.get("HOLD_TYPE")),
                    "changeRatio": _number(row.get("CHANGE_RATIO")),
                    "holdBefore": _number(row.get("BEGIN_HOLD_NUM")),
                    "holdAfter": _number(row.get("END_HOLD_NUM")),
                    "changeSharesSigned": shares,
                    "direction": direction,
                    "vendorId": change_id, "vendorCode": _clean(row.get("DERIVE_SECURITY_CODE")),
                    "vendorReport": REPORT, "windowStart": since, "windowEnd": until,
                    "batchSize": limit, "vendorCount": (result or {}).get("count"),
                    "missingPolicy": ("a field the vendor leaves empty is skipped, never zeroed; "
                                      "no ratio is derived from a missing holding"),
                    "authorityNote": L2_UPGRADE_NOTE,
                },
            ))
    if not records:
        raise net.FetchError(
            f"eastmoney_source_gap: {thscode} has rows but none inside {since}..{until} "
            "carried a usable share count")
    return FetchResult(records)


def _number(value) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"[,%\s]", "", str(value))
    if text in {"", "-", "--"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _day(value) -> str:
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    return match.group(0) if match else ""


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()
