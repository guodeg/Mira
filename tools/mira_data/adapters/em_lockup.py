"""限售解禁 (lock-up expiry) schedule from the Eastmoney datacenter relay (L5).

The share-supply input that ``market-structure-policy.md`` asks for: when restricted shares
become tradable, how many holders are behind the batch, and what kind of shares they are.

**What this module deliberately claims, and what it refuses to claim.** The live probe
(2026-10-03) produced rows whose unit basis does not reconcile with each other: for 300750 on
2024-09-24 the relay reports ``FREE_SHARES=390146.5544``, ``FREE_RATIO=0.000822`` and
``ALIFT_MARKET_CAP=63369.73``. Read as shares and a fraction of total shares, the first two imply
a denominator of roughly 4.75e8 shares, which is not that issuer's share count; read as 万股 they
disagree by two orders of magnitude instead. So this channel claims only the fields whose meaning
is self-evident - the expiry **date**, the **share type**, and the **number of holders** in the
batch - and carries the three ambiguous values in provenance under ``vendorRawUnverified`` with
``unitBasis`` saying plainly that they are unverified. Nothing is converted, and no ratio is
derived from them.

Verified contract::

    GET https://datacenter-web.eastmoney.com/api/data/v1/get
        reportName=RPT_LIFT_STAGE & columns=ALL & pageNumber=1 & pageSize=N
        sortColumns=FREE_DATE & sortTypes=-1 & source=WEB & client=WEB
        filter=(SECURITY_CODE="300750")        -> {"result": {"count": 15, "data": [...]}}
    row: FREE_DATE "2024-09-24 00:00:00", FREE_SHARES_TYPE "股权激励限售股份",
         BATCH_HOLDER_NUM 1, FREE_SHARES 390146.5544, FREE_RATIO 0.000822,
         ALIFT_MARKET_CAP 63369.73, ABLE_FREE_SHARES, CURRENT_FREE_SHARES, A20/B20_ADJCHRATE

Seven sibling report names were probed and do not exist (``RPT_LIFTING_STAGE``,
``RPT_LIFT_BAN_STAGE``, ``RPT_RESTRICTED_RELEASE_STAGE``, ``RPT_LIFT_STAGE_DETAILS``,
``RPT_UNLOCK_STAGE``, ``RPT_LIFT_STAGE_NEW``, ``RPT_LIFTINGBAN_STAGE``), so the search does not
need repeating. The filter takes the bare six-digit code, as on the other relays.
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
REPORT = "RPT_LIFT_STAGE"
ENDPOINT = "eastmoney://lift-stage/{symbol}"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Referer": "https://data.eastmoney.com/",
}
DEFAULT_MAX_EVENTS = 20
UNVERIFIED_FIELDS = ("FREE_SHARES", "FREE_RATIO", "ALIFT_MARKET_CAP")
UNIT_BASIS = ("unverified: FREE_SHARES/FREE_RATIO/ALIFT_MARKET_CAP do not reconcile with each "
              "other or with the issuer's share count in the live sample, so they are carried as "
              "raw vendor values only and no share quantity, ratio or market value is claimed")
UPGRADE_NOTE = ("an unlock is also disclosed by the issuer and hosted by the exchange/CNINFO; "
                "that filing is the L2 route to a verified share quantity")


def _max_events(limit: Optional[int]) -> int:
    if limit is not None:
        return max(1, min(200, int(limit)))
    try:
        return max(1, min(200, int((config.get("MIRA_EM_LOCKUP_MAX_EVENTS")
                                    or str(DEFAULT_MAX_EVENTS)).strip())))
    except ValueError:
        return DEFAULT_MAX_EVENTS


def fetch_lockup_schedule(
    symbol: str,
    *,
    since: Optional[str] = None,
    until: Optional[str] = None,
    max_items: Optional[int] = None,
    as_of: Optional[str] = None,
    market_scope: str = "CN",
) -> FetchResult:
    """One record per unlock batch, claiming only the fields whose units are self-evident."""
    as_of = as_of or _dt.date.today().isoformat()
    thscode = resolve_thscode(symbol)
    code, _board = thscode.split(".")
    since = since or "1990-01-01"
    until = until or (_dt.date.fromisoformat(as_of) + _dt.timedelta(days=730)).isoformat()
    limit = _max_events(max_items)

    params = {
        "reportName": REPORT, "columns": "ALL", "pageNumber": "1", "pageSize": str(limit),
        "sortColumns": "FREE_DATE", "sortTypes": "-1", "source": "WEB", "client": "WEB",
        "filter": f'(SECURITY_CODE="{code}")',
    }
    payload = net.get_json(URL + "?" + urllib.parse.urlencode(params), headers=HEADERS,
                           retries=2, backoff=1.5)
    result = payload.get("result") if isinstance(payload, dict) else None
    rows = (result or {}).get("data") or []
    if not rows:
        raise net.FetchError(
            f"eastmoney_source_gap: no lock-up expiry row reported for {thscode}")

    posture = POSTURES["em_lockup_schedule"]
    records: list[CanonicalRecord] = []
    for row in rows:
        day = _day(row.get("FREE_DATE"))
        if not day or day < since or day > until:
            continue
        share_type = _clean(row.get("FREE_SHARES_TYPE")) or "未标注股份类型"
        holders = _number(row.get("BATCH_HOLDER_NUM"))
        records.append(CanonicalRecord(
            family="ownership_short_interest", research_object=thscode,
            market_scope=market_scope, metric="lockup_expiry_holders", value=holders or 0.0,
            unit="holders", period=day, period_type="point_in_time", as_of_date=as_of,
            source_date=day, posture=posture, url_or_path=ENDPOINT.format(symbol=thscode),
            claim_text=(f"{thscode} {day} 解禁：{share_type}"
                        + (f"，{int(holders)} 名股东" if holders else "")),
            provenance={
                "shareType": share_type, "holders": holders,
                "vendorRawUnverified": {field: row.get(field) for field in UNVERIFIED_FIELDS},
                "unitBasis": UNIT_BASIS, "upgradePath": UPGRADE_NOTE,
                "windowStart": since, "windowEnd": until, "batchSize": limit,
                "vendorCount": (result or {}).get("count"),
                "claimScope": ("date, share type and holder count are claimed; quantity, ratio "
                               "and market value are not"),
            },
        ))
    if not records:
        raise net.FetchError(
            f"eastmoney_source_gap: {thscode} has lock-up rows but none with a usable date "
            f"inside {since}..{until}")
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
