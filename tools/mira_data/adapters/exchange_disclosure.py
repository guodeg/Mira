"""Exchange-direct disclosure metadata: SSE and SZSE announcement indexes.

The second independent entry point to the same filings the CNINFO channel already reads. The
protocol's A-share pack names all three (``cninfo_a_share_disclosure_search``,
``sse_listed_company_announcements``, ``szse_listed_company_announcements``), so this is the
cross-check half rather than a new claim: when CNINFO and the exchange disagree about whether a
filing exists, the disagreement is visible instead of silent.

Policy followed (reviewed by an independent project that publishes its source contracts): the
exchanges are **metadata only**. Titles, dates and document links are read; document bodies are
not fetched or redistributed, because commercial reuse of the bodies can require written
permission. Every record therefore says ``bodyRetrieval=link_only`` rather than implying the
document was ingested.

Verified contracts (probed live 2026-10-02)
-------------------------------------------
SSE ``GET https://query.sse.com.cn/security/stock/queryCompanyBulletin.do``::

    isPagination=true & productId=<6-digit code>
    securityType=0101,120100,020100,020200,120200
    beginDate=YYYY-MM-DD & endDate=YYYY-MM-DD
    pageHelp.pageSize=N & pageHelp.pageNo=1 & pageHelp.beginPage=1 & pageHelp.endPage=1
    -> {"result": [{SECURITY_CODE, SECURITY_NAME, TITLE, URL, SSEDATE, ADDDATE, ...}],
        "pageHelp": {"total": n, ...}}

Rows carry both ``SSEDATE`` (a day) and ``ADDDATE`` (a timestamp); the day is used for the
disclosure date and the timestamp is kept in provenance. ``URL`` is a site path
(``/disclosure/listedinfo/announcement/...``) that only resolves against ``www.sse.com.cn``.

SZSE ``POST https://www.szse.cn/api/disc/announcement/annList?random=0.5``::

    {"seDate": ["YYYY-MM-DD", "YYYY-MM-DD"], "channelCode": ["listedNotice_disc"],
     "stock": ["000001"], "pageSize": N, "pageNum": 1}
    -> {"announceCount": n, "data": [{annId, title, publishTime, attachPath, attachFormat,
        secCode: ["000001"], secName: ["平安银行"], id, ...}]}

``attachPath`` (``/disc/.../XXXX.PDF``) only resolves against ``disc.static.szse.cn/download``.

Both reads are **bounded**: one page, an explicit window, and a caller-set item cap, because the
registry rows are on-demand entry points rather than a licence to crawl. Beijing-exchange names
have neither endpoint and degrade to a labelled gap without spending a request.
"""

from __future__ import annotations

import datetime as _dt
import re
import urllib.parse
from typing import Optional

from .. import config, net
from ..canonical import POSTURES, CanonicalRecord, FetchResult
from .hithink_finance import resolve_thscode

SSE_URL = "https://query.sse.com.cn/security/stock/queryCompanyBulletin.do"
SZSE_URL = "https://www.szse.cn/api/disc/announcement/annList"
SSE_DOC_HOST = "https://www.sse.com.cn"
SZSE_DOC_HOST = "https://disc.static.szse.cn/download"
SSE_ENDPOINT = "sse-disclosure://company-bulletin/{symbol}"
SZSE_ENDPOINT = "szse-disclosure://announcement-annList/{symbol}"
SSE_SECURITY_TYPES = "0101,120100,020100,020200,120200"
SZSE_CHANNELS = ["listedNotice_disc"]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")
SSE_HEADERS = {"User-Agent": UA, "Accept": "application/json",
               "Referer": "https://www.sse.com.cn/"}
SZSE_HEADERS = {"User-Agent": UA, "Accept": "application/json",
                "Content-Type": "application/json",
                "Referer": "https://www.szse.cn/disclosure/listed/notice/index.html"}
DEFAULT_DAYS = 90
DEFAULT_MAX_ITEMS = 50
BODY_RETRIEVAL = "link_only"
_DAY = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_SSE_SAFE_URL = re.compile(r"^/disclosure/listedinfo/announcement/[A-Za-z0-9/_.-]+\.pdf$", re.I)
_SZSE_SAFE_URL = re.compile(r"^/disc/[A-Za-z0-9/_.-]+\.pdf$", re.I)


def _days() -> int:
    try:
        return max(1, min(400, int((config.get("MIRA_EXCHANGE_WINDOW_DAYS")
                                    or str(DEFAULT_DAYS)).strip())))
    except ValueError:
        return DEFAULT_DAYS


def _cap(max_items: Optional[int]) -> int:
    if max_items is not None:
        return max(1, min(200, int(max_items)))
    try:
        return max(1, min(200, int((config.get("MIRA_EXCHANGE_MAX_ITEMS")
                                    or str(DEFAULT_MAX_ITEMS)).strip())))
    except ValueError:
        return DEFAULT_MAX_ITEMS


def fetch_exchange_announcements(
    symbol: str,
    *,
    since: Optional[str] = None,
    until: Optional[str] = None,
    max_items: Optional[int] = None,
    as_of: Optional[str] = None,
    market_scope: str = "CN",
) -> FetchResult:
    """Announcement metadata for one A-share name from the listing exchange itself."""
    as_of = as_of or _dt.date.today().isoformat()
    thscode = resolve_thscode(symbol)
    code, board = thscode.split(".")
    end = until or as_of
    start = since or (_dt.date.fromisoformat(end) - _dt.timedelta(days=_days())).isoformat()
    cap = _cap(max_items)

    if board == "SH":
        records = _sse_records(code, start=start, end=end, cap=cap, thscode=thscode,
                               as_of=as_of, market_scope=market_scope)
        endpoint = SSE_ENDPOINT
    elif board == "SZ":
        records = _szse_records(code, start=start, end=end, cap=cap, thscode=thscode,
                                as_of=as_of, market_scope=market_scope)
        endpoint = SZSE_ENDPOINT
    else:
        raise net.FetchError(
            f"exchange_disclosure_source_gap: the Beijing exchange publishes no per-issuer "
            f"announcement endpoint in this substrate ({thscode}); use the CNINFO channel")
    if not records:
        raise net.FetchError(
            f"exchange_disclosure_source_gap: {thscode} has no exchange-published announcement "
            f"between {start} and {end} (the window is explicit; widen --since)")
    records.sort(key=lambda record: record.source_date, reverse=True)
    return FetchResult(records[:cap])


def _sse_records(code: str, *, start: str, end: str, cap: int, thscode: str, as_of: str,
                 market_scope: str) -> list[CanonicalRecord]:
    posture = POSTURES["sse_announcement"]
    url = SSE_URL + "?" + urllib.parse.urlencode({
        "isPagination": "true", "productId": code, "securityType": SSE_SECURITY_TYPES,
        "beginDate": start, "endDate": end,
        "pageHelp.pageSize": str(cap), "pageHelp.pageNo": "1",
        "pageHelp.beginPage": "1", "pageHelp.endPage": "1",
    })
    payload = net.get_json(url, headers=SSE_HEADERS, retries=2, backoff=1.5)
    rows = payload.get("result") or []
    total = (payload.get("pageHelp") or {}).get("total")
    records = []
    seen: set[str] = set()
    for row in rows:
        day = _day(row.get("SSEDATE")) or _day(row.get("ADDDATE"))
        title = _clean(row.get("TITLE"))
        path = str(row.get("URL") or "").strip()
        if not day or not title or not path:
            continue
        key = _sse_announcement_id(path) or f"{day}|{title}"
        if key in seen:
            continue
        seen.add(key)
        records.append(CanonicalRecord(
            family="issuer_disclosure", research_object=thscode, market_scope=market_scope,
            metric="announcement", value=key, unit="announcement_id",
            period=day, period_type="point_in_time", as_of_date=as_of, source_date=day,
            posture=posture, url_or_path=endpoint_for(SSE_ENDPOINT, thscode),
            claim_text=f"{_clean(row.get('SECURITY_NAME')) or code} {day} 公告：{title}",
            provenance={
                "exchange": "SSE", "secCode": str(row.get("SECURITY_CODE") or code),
                "channel": "exchange_direct", "bodyRetrieval": BODY_RETRIEVAL,
                "documentUrl": SSE_DOC_HOST + path if _SSE_SAFE_URL.match(path) else None,
                "documentPath": path,
                "disclosureTime": _clean(row.get("ADDDATE")),
                "pageTotal": total, "windowStart": start, "windowEnd": end,
                "publisherNote": ("announcement metadata from the listing exchange; document "
                                  "bodies are not fetched or redistributed"),
            },
        ))
        if len(records) >= cap:
            break
    return records


def _szse_records(code: str, *, start: str, end: str, cap: int, thscode: str, as_of: str,
                  market_scope: str) -> list[CanonicalRecord]:
    posture = POSTURES["szse_announcement"]
    url = SZSE_URL + "?random=0.5"
    payload = net.post_json(url, {
        "seDate": [start, end], "channelCode": SZSE_CHANNELS, "stock": [code],
        "pageSize": cap, "pageNum": 1,
    }, headers=SZSE_HEADERS, retries=2, backoff=1.5)
    rows = payload.get("data") or []
    total = payload.get("announceCount")
    records = []
    seen: set[str] = set()
    for row in rows:
        day = _day(row.get("publishTime"))
        title = _clean(row.get("title"))
        ann_id = row.get("annId")
        if not day or not title or ann_id in (None, ""):
            continue
        key = str(ann_id)
        if key in seen:
            continue
        seen.add(key)
        path = str(row.get("attachPath") or "").strip()
        codes = row.get("secCode") or []
        records.append(CanonicalRecord(
            family="issuer_disclosure", research_object=thscode, market_scope=market_scope,
            metric="announcement", value=key, unit="announcement_id",
            period=day, period_type="point_in_time", as_of_date=as_of, source_date=day,
            posture=posture, url_or_path=endpoint_for(SZSE_ENDPOINT, thscode),
            claim_text=f"{_clean((row.get('secName') or [''])[0]) or code} {day} 公告：{title}",
            provenance={
                "exchange": "SZSE", "secCodes": [str(item) for item in codes],
                "channel": "exchange_direct", "bodyRetrieval": BODY_RETRIEVAL,
                "documentUrl": (SZSE_DOC_HOST + path if _SZSE_SAFE_URL.match(path) else None),
                "documentFormat": _clean(row.get("attachFormat")),
                "documentPath": path,
                "vendorId": _clean(row.get("id")),
                "announceCount": total, "windowStart": start, "windowEnd": end,
                "publisherNote": ("announcement metadata from the listing exchange; document "
                                  "bodies are not fetched or redistributed"),
            },
        ))
        if len(records) >= cap:
            break
    return records


def endpoint_for(template: str, thscode: str) -> str:
    return template.format(symbol=thscode)


def _sse_announcement_id(path: str) -> str:
    match = re.search(r"/([^/]+)\.pdf$", path, re.I)
    return match.group(1) if match else ""


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _day(value) -> str:
    match = _DAY.search(str(value or ""))
    return match.group(0) if match else ""
