"""CNINFO (巨潮资讯网) announcement index adapter -> canonical ``issuer_disclosure``.

CNINFO is the disclosure portal designated for mainland listed companies, so this
is the A-share **L1** channel Mira was missing: 公告索引（定期报告、业绩预告、回购、
减持、中标、诉讼、股东大会…）with enough identity to cite — ``secCode`` +
``announcementId`` + title + disclosure date + PDF URL.

What it emits (and what it does not)
------------------------------------
One ``issuer_disclosure`` record per announcement: the *disclosure event* and its
identity. It does **not** parse the PDF body, so it cannot support a revenue or
margin claim on its own — it tells you which primary document exists, when it was
disclosed and where to read it. Extracting numbers from the body is a separate,
optional step (PyMuPDF is AGPL, so it must stay an optional import and must never
be vendored).

Verified contract (probed live 2026-10-02; no cookie, token or JS challenge)
--------------------------------------------------------------------------
- index: ``POST /new/hisAnnouncement/query`` (form-encoded). ``pageSize`` is
  **server-capped at 30** regardless of what you send; page with ``pageNum`` until
  ``hasMore`` is false.
- per-stock queries need ``stock="<6-digit>,<orgId>"``; a bare 6-digit code returns
  **zero rows**. The orgId map comes in one request from ``/new/data/szse_stock.json``
  (6259 rows) or per code from ``/new/information/topSearch/query``.
- PDF: ``http://static.cninfo.com.cn/`` + the relative ``adjunctUrl`` (verified
  HTTP 200 / application/pdf).
- ``plate`` accepts ``sz``/``sh``/``bj``; ``bj`` (北交所) works despite being
  undocumented upstream.

Traps this adapter exists to absorb
-----------------------------------
1. **Invalid category codes fail silently.** ``category_yjkb_szsh`` (the "业绩快报"
   code printed in public write-ups) and any bogus string return the *whole market*
   (157,199 rows) instead of erroring. Categories are therefore whitelisted here and
   the known-bad ones raise instead of quietly widening the query.
2. **``announcementTime`` is a UTC epoch.** Read as UTC, every disclosure date lands
   one day early; it must be decoded in Asia/Shanghai (same class of bug as the
   vendor epochs in ``hithink_finance``).
3. **Duplicates exist but titles alone cannot detect them.** The same PDF can be
   indexed twice under different ``announcementId``; conversely the *same title on
   different dates is two real announcements* (observed: 600519 董事会秘书 聘任公告
   on 2026-05-22 and 2026-06-12). Dedupe on (secCode, Beijing date, normalized title).
4. **Titles vary by board** (创业板/科创板 use the bare title, 沪市老牌国企 prepend
   the full company name) and carry ``<em>`` highlight tags when ``searchkey`` is used.

Event classification
--------------------
``announcementType`` turned out to be a pipe-delimited **multi-label** code list
(``01010503||010113||011513``), not the single opaque code the public write-ups
describe — so it beats title regexes for the cases verified below. It has **no public
ontology**, so the table is deliberately small and every entry records the sample it
came from; anything unmapped falls through to title rules and finally to ``other``.
Guessing a token would be worse than admitting ``other``.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import time
from pathlib import Path
from typing import Iterator, Optional

from .. import config, net
from ..canonical import POSTURES, CanonicalRecord, FetchResult
from .hithink_finance import resolve_thscode  # shared A-share symbol -> thscode map

QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
TOPSEARCH_URL = "http://www.cninfo.com.cn/new/information/topSearch/query"
ORGID_MAP_URL = "http://www.cninfo.com.cn/new/data/szse_stock.json"
PDF_BASE = "http://static.cninfo.com.cn/"
ENDPOINT = "cninfo://hisAnnouncement/query/{thscode}"

# Any browser UA clears the (light) throttle; a bare urllib UA is riskier.
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
HEADERS = {"User-Agent": BROWSER_UA, "X-Requested-With": "XMLHttpRequest"}

PAGE_SIZE = 30            # server-enforced; sending more does nothing
DEFAULT_DAYS = 365
DEFAULT_MAX_ITEMS = 100
MAX_PAGES = 60            # bounded on-demand read, never a full-market backfill
CN_TZ = _dt.timezone(_dt.timedelta(hours=8))

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ORGID_CACHE_PATH = _REPO_ROOT / "local" / "mira-data-cache" / "cninfo-orgid-map.json"
_ORGID_CACHE_MAX_AGE = _dt.timedelta(days=7)

# Verified live 2026-10-02 (counts are for plate=sh, 2026-04-01~2026-10-02).
CATEGORY_TOKENS = {
    "category_ndbg_szsh": ("periodic_report_annual", 3596),
    "category_yjdbg_szsh": ("periodic_report_q1", 2344),
    "category_bndbg_szsh": ("periodic_report_h1", 4670),
    "category_sjdbg_szsh": ("periodic_report_q3", 19),
    "category_yjygjxz_szsh": ("earnings_preannouncement", 1024),
    "category_qyfpxzcs_szsh": ("dividend", 4305),
    "category_gqjl_szsh": ("equity_incentive", 6535),
    "category_gddh_szsh": ("shareholder_meeting", 18114),
    "category_dshgg_szsh": ("board_resolution", 9524),
    "category_zj_szsh": ("intermediary_report", 21564),
}
# Codes that do NOT filter server-side: they return the full market silently.
BROKEN_CATEGORIES = {
    "category_yjkb_szsh": "documented as 业绩快报 but returns the whole market",
    "category_jshgg_szsh": "supervisory-board code seen in write-ups but returns the whole market",
}

# announcementType sub-code -> event token. Each line names the observed sample.
TYPE_TOKENS = {
    "011513": "buyback",                    # 回购实施进展公告 / 回购进展公告
    "011501": "shareholder_reduction",      # 减持股份结果公告 (x4)
    "012111": "earnings_preannouncement",   # 业绩预告的自愿性披露公告
    "012327": "contract_award",             # 项目中标公告 (x3)
    "012309": "litigation",                 # 诉讼进展公告 / 累计诉讼、仲裁
    "012311": "litigation",                 # co-occurs with 012309 on 仲裁 rows
    "011301": "dividend",                   # 利润分配方案公告
    "012903": "intermediary_report",        # 法律意见书 (x2)
    "01010901": "intermediary_report",      # 法律意见书 / 差异化分红 (x2)
    "010109": "intermediary_report",        # 核查意见
    "010303": "periodic_report_h1",         # 半年度报告 (single sample: treat as a hint)
}
# Codes that look specific but are not: 012399 was first seen on 业绩说明会预告公告 (x3) and
# mapped to earnings_briefing, but later probes found the same code on 投资者关系活动记录表
# (5 Shanghai + 1 Shenzhen rows) and even on a 股东会通知. It is a broad IR/governance bucket,
# so it must defer to the title instead of minting a specific claim token.
AMBIGUOUS_TYPE_CODES = {"012399"}
# Present on nearly every record / merely encode the venue; they carry no signal.
TYPE_NOISE = {"01010503", "010113", "010123", "010112", "010115", "01010501"}

# Fallback when announcementType says nothing usable. Ordered: first match wins.
TITLE_RULES: list[tuple[str, str]] = [
    (r"\d{4}年年度报告", "periodic_report_annual"),
    (r"\d{4}年半年度报告", "periodic_report_h1"),
    (r"\d{4}年第一季度报告", "periodic_report_q1"),
    (r"\d{4}年第三季度报告", "periodic_report_q3"),
    (r"业绩说明会|业绩暨.*说明会", "earnings_briefing"),
    (r"投资者关系活动记录表|调研活动记录|接待调研记录", "investor_relations_record"),
    (r"业绩预告", "earnings_preannouncement"),
    (r"业绩快报", "earnings_flash"),
    (r"回购", "buyback"),
    (r"减持", "shareholder_reduction"),
    (r"增持", "shareholder_increase"),
    (r"利润分配|权益分派|分红", "dividend"),
    (r"股东大会|股东会", "shareholder_meeting"),
    (r"董事会.*决议|董事会公告", "board_resolution"),
    (r"监事会", "supervisory_resolution"),
    # Trading-status and enforcement events. These were missing until the exchange-direct
    # research surfaced the reviewed title taxonomy used for the same filings there, and they
    # are the events a monitoring loop actually keys on: a halt changes tradability today, an
    # investigation or a pledge changes the risk picture.
    (r"停牌|复牌", "trading_halt"),
    (r"质押", "share_pledge"),
    (r"立案调查|立案告知书|调查通知书", "investigation"),
    (r"风险提示|异常波动|退市风险警示", "risk_alert"),
    (r"股权激励|限制性股票|激励计划", "equity_incentive"),
    (r"中标|中选|合同|订单", "contract_award"),
    (r"诉讼|仲裁", "litigation"),
    (r"解禁|上市流通", "share_unlock"),
    # A-share specific and unambiguous; the type codes for these are unmapped
    # (single samples only), and a title rule beats admitting "other" for them.
    # 产销快报 / 主要经营数据 is a monthly operating datapoint many A-share names publish
    # ahead of their filings, so it is worth a token of its own.
    (r"主要经营数据|产销快报|产量.{0,4}销量", "operating_data_release"),
    (r"H股公告|H股通函|H股月报表", "cross_listing_filing"),
    (r"关联交易", "related_party_transaction"),
    (r"对外担保|提供担保|担保额度", "guarantee"),
    (r"会计师事务所", "auditor_engagement"),
    (r"会计政策变更|会计估计变更", "accounting_change"),
    (r"聘任|辞职|离任|选举", "management_change"),
]
# Periodic-report shells that must not be classified as the report body itself.
_REPORT_SHELLS = ("摘要", "审计报告", "内部控制", "鉴证报告", "提示性公告", "更正")
# Governance documents that merely mention 调研/回购/减持 etc.; they are not events.
_POLICY_DOCS = ("管理办法", "管理制度", "工作细则", "工作制度", "议事规则",
                "实施细则", "公司章程", "章程", "声明与承诺", "工作规则")
_EM_TAG = re.compile(r"</?em>")


def _setting(name: str, default: str) -> str:
    return (config.get(name) or default).strip()


def _timeout() -> int:
    try:
        return int(_setting("MIRA_CNINFO_TIMEOUT", "25"))
    except ValueError:
        return 25


def _page_sleep() -> float:
    try:
        return float(_setting("MIRA_CNINFO_PAGE_SLEEP", "0.4"))
    except ValueError:
        return 0.4


def _max_items(limit: Optional[int]) -> int:
    if limit is not None:
        return max(1, int(limit))
    try:
        return max(1, int(_setting("MIRA_CNINFO_MAX_ITEMS", str(DEFAULT_MAX_ITEMS))))
    except ValueError:
        return DEFAULT_MAX_ITEMS


# --------------------------------------------------------------------------- #
# orgId resolution
# --------------------------------------------------------------------------- #

def org_id_map(*, force_refresh: bool = False) -> dict[str, str]:
    """``{secCode: orgId}`` for the whole market, cached for a week.

    One request replaces 5000+ per-stock lookups; the cache mirrors the SEC
    ticker-map pattern (``sec_companyfacts._read_ticker_cache``).
    """
    if not force_refresh:
        cached = _read_orgid_cache()
        if cached:
            return cached
    payload = net.get_json(ORGID_MAP_URL, headers=HEADERS, timeout=_timeout())
    rows = payload.get("stockList") if isinstance(payload, dict) else None
    mapping = {
        str(row.get("code")).strip(): str(row.get("orgId")).strip()
        for row in (rows or [])
        if isinstance(row, dict) and row.get("code") and row.get("orgId")
    }
    if not mapping:
        raise net.FetchError("cninfo_orgid_gap: org id map returned no usable rows")
    _write_orgid_cache(mapping)
    return mapping


def resolve_org_id(sec_code: str) -> str:
    """orgId for one 6-digit A-share code (bulk map first, topSearch fallback)."""
    code = sec_code.strip()
    known = org_id_map().get(code)
    if known:
        return known
    payload = net.post_form_json(TOPSEARCH_URL, {"keyWord": code, "maxNum": "10"},
                                headers=HEADERS, timeout=_timeout())
    for row in payload if isinstance(payload, list) else []:
        if isinstance(row, dict) and str(row.get("code")).strip() == code and row.get("orgId"):
            return str(row["orgId"]).strip()
    raise net.FetchError(f"cninfo_orgid_gap: no orgId for {code} (not an A-share code?)")


def _read_orgid_cache() -> dict[str, str]:
    try:
        modified = _dt.datetime.fromtimestamp(_ORGID_CACHE_PATH.stat().st_mtime)
        if _dt.datetime.now() - modified > _ORGID_CACHE_MAX_AGE:
            return {}
        payload = json.loads(_ORGID_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(payload, dict) or not payload:
        return {}
    return {str(k): str(v) for k, v in payload.items() if k and v}


def _write_orgid_cache(mapping: dict[str, str]) -> None:
    try:
        _ORGID_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = _ORGID_CACHE_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(mapping, sort_keys=True), encoding="utf-8")
        temporary.replace(_ORGID_CACHE_PATH)
    except OSError:
        return  # cache is an optimization; a read-only checkout must still work


# --------------------------------------------------------------------------- #
# query
# --------------------------------------------------------------------------- #

def _query_page(*, stock: str = "", plate: str = "", category: str = "",
                se_date: str = "", page_num: int = 1, column: str = "szse") -> dict:
    if category and category in BROKEN_CATEGORIES:
        raise net.FetchError(
            f"cninfo_category_gap: {category} does not filter server-side "
            f"({BROKEN_CATEGORIES[category]}); omit it and filter locally")
    if category and category not in CATEGORY_TOKENS:
        raise net.FetchError(
            f"cninfo_category_gap: unknown category {category!r}; an unrecognised code "
            "silently returns the whole market, so only whitelisted codes are allowed")
    body = {
        "tabName": "fulltext", "pageSize": str(PAGE_SIZE), "pageNum": str(page_num),
        "column": column, "category": category, "plate": plate, "searchkey": "",
        "secid": "", "trade": "", "seDate": se_date, "stock": stock,
        "sortName": "", "sortType": "", "isHLtitle": "true",
    }
    payload = net.post_form_json(QUERY_URL, body, headers=HEADERS, timeout=_timeout())
    if not isinstance(payload, dict):
        raise net.FetchError("cninfo_bad_output: query did not return an object")
    return payload


def _iter_announcements(*, stock: str = "", plate: str = "", category: str = "",
                        se_date: str = "", max_items: int = DEFAULT_MAX_ITEMS,
                        max_pages: int = MAX_PAGES) -> Iterator[dict]:
    page, yielded = 1, 0
    while page <= max_pages and yielded < max_items:
        payload = _query_page(stock=stock, plate=plate, category=category,
                              se_date=se_date, page_num=page)
        items = payload.get("announcements") or []
        if not items:
            return
        for item in items:
            if yielded >= max_items:
                return
            yielded += 1
            yield item
        if not payload.get("hasMore"):
            return
        page += 1
        pause = _page_sleep()
        if pause > 0:
            _sleep(pause)


def _sleep(seconds: float) -> None:
    # Wrapped so tests can monkeypatch; keeps the run inside polite pacing.
    time.sleep(min(seconds, 5.0))


# --------------------------------------------------------------------------- #
# classification + record building
# --------------------------------------------------------------------------- #

def clean_title(raw: Optional[str]) -> str:
    """Drop the ``<em>`` highlight tags CNINFO injects when searchkey matches."""
    return _EM_TAG.sub("", raw or "").strip()


def classify(announcement: dict, *, category: str = "") -> tuple[str, str]:
    """Return ``(event_token, basis)``; never guess — unmapped input becomes ``other``."""
    title = clean_title(announcement.get("announcementTitle"))
    if category and category in CATEGORY_TOKENS:
        token = CATEGORY_TOKENS[category][0]
        # A periodic-report category also contains 摘要/审计报告/内控/英文版 shells, so
        # the category alone must not label a summary as the report body.
        if token.startswith("periodic_report") and _is_report_shell(title):
            return "other", f"category:{category}:report_shell"
        return token, "category"
    codes = [code.strip() for code in str(announcement.get("announcementType") or "").split("||")]
    for code in codes:
        token = TYPE_TOKENS.get(code)
        if token:
            return token, f"announcement_type:{code}"
    if _is_policy_doc(title):
        return "other", "title:policy_document"
    if _is_report_shell(title) and re.search(r"\d{4}年(年度|半年度|第一季度|第三季度)报告", title):
        return "other", "title:report_shell"
    for pattern, token in TITLE_RULES:
        if re.search(pattern, title):
            return token, "title"
    if any(code in AMBIGUOUS_TYPE_CODES for code in codes):
        # The vendor bucket covers 业绩说明会 / 投资者关系活动记录表 / 股东会 alike, and the
        # title matched none of them, so no specific token is defensible here.
        return "other", "announcement_type:ambiguous(012399)"
    return "other", "unknown"


def _is_report_shell(title: str) -> bool:
    return any(shell in title for shell in _REPORT_SHELLS)


def _is_policy_doc(title: str) -> bool:
    """管理办法/制度类文件提到调研，但不是一次调研记录。"""
    return any(marker in title for marker in _POLICY_DOCS)


def bj_date(epoch_ms) -> str:
    """Decode a CNINFO epoch (UTC ms) as an Asia/Shanghai calendar date."""
    if epoch_ms is None:
        return ""
    try:
        return _dt.datetime.fromtimestamp(int(epoch_ms) / 1000, tz=CN_TZ).date().isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def bj_time(epoch_ms) -> str:
    if epoch_ms is None:
        return ""
    try:
        return _dt.datetime.fromtimestamp(int(epoch_ms) / 1000, tz=CN_TZ).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def pdf_url(adjunct_path: Optional[str]) -> str:
    """``adjunctUrl`` is relative; the direct PDF link is PDF_BASE + path."""
    return PDF_BASE + (adjunct_path or "").lstrip("/")


def dedupe_key(announcement: dict) -> tuple:
    return (
        str(announcement.get("secCode") or ""),
        bj_date(announcement.get("announcementTime")),
        clean_title(announcement.get("announcementTitle")),
    )


def _record(announcement: dict, *, thscode: str, market_scope: str, as_of: str,
            category: str = "") -> CanonicalRecord:
    token, basis = classify(announcement, category=category)
    title = clean_title(announcement.get("announcementTitle"))
    date = bj_date(announcement.get("announcementTime"))
    announcement_id = str(announcement.get("announcementId") or "")
    url = pdf_url(announcement.get("adjunctUrl"))
    return CanonicalRecord(
        family="issuer_disclosure", research_object=thscode, market_scope=market_scope,
        metric=token, value=announcement_id, unit="announcement_id",
        period=date, period_type="point_in_time", as_of_date=as_of, source_date=date,
        posture=POSTURES["cninfo_disclosure"], url_or_path=url,
        claim_text=f"{thscode} {token}《{title}》 ({date}, CNINFO {announcement_id})",
        provenance={
            "secCode": announcement.get("secCode"), "secName": announcement.get("secName"),
            "orgId": announcement.get("orgId"), "announcementId": announcement_id,
            "announcementType": announcement.get("announcementType"),
            "pageColumn": announcement.get("pageColumn"),
            "adjunctUrl": announcement.get("adjunctUrl"),
            "adjunctSize": announcement.get("adjunctSize"),
            "adjunctType": announcement.get("adjunctType"),
            "announcement_time_bj": bj_time(announcement.get("announcementTime")),
            "event_basis": basis,
            "title_zh": title,
        },
    )


def fetch_issuer_disclosures(
    symbol: str,
    *,
    as_of: Optional[str] = None,
    market_scope: str = "CN",
    since: Optional[str] = None,
    until: Optional[str] = None,
    category: str = "",
    max_items: Optional[int] = None,
) -> FetchResult:
    """Announcement index for one A-share name (on demand, single name only).

    ``since``/``until`` are ``YYYY-MM-DD``; the default window is the last year.
    """
    as_of = as_of or _dt.date.today().isoformat()
    thscode = resolve_thscode(symbol)
    sec_code, board = thscode.split(".")
    org = resolve_org_id(sec_code)

    until = until or as_of
    since = since or (_dt.date.fromisoformat(until) - _dt.timedelta(days=DEFAULT_DAYS)).isoformat()
    se_date = f"{since}~{until}"

    seen: dict[tuple, CanonicalRecord] = {}
    for announcement in _iter_announcements(
        stock=f"{sec_code},{org}", category=category, se_date=se_date,
        max_items=_max_items(max_items),
    ):
        key = dedupe_key(announcement)
        record = _record(announcement, thscode=thscode, market_scope=market_scope,
                         as_of=as_of, category=category)
        existing = seen.get(key)
        # Same PDF indexed twice keeps the smaller announcementId (earlier ingest).
        if existing is None or _id_sort_key(record.value) < _id_sort_key(existing.value):
            seen[key] = record

    records = sorted(seen.values(), key=lambda r: (r.period, _id_sort_key(r.value)))
    if not records:
        raise net.FetchError(
            f"cninfo_source_gap: no announcements for {thscode} in {se_date}"
            + (f" (category {category})" if category else ""))
    return FetchResult(records)


def _id_sort_key(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def probe() -> dict:
    """Cheap health check: org-id map reachable + one live index query."""
    mapping = org_id_map()
    sample_code = "600519"
    org = mapping.get(sample_code) or resolve_org_id(sample_code)
    payload = _query_page(stock=f"{sample_code},{org}", page_num=1)
    announcements = payload.get("announcements") or []
    return {
        "org_ids": len(mapping),
        "sample_code": sample_code,
        "sample_org_id": org,
        "sample_total": payload.get("totalAnnouncement"),
        "sample_first": clean_title(announcements[0].get("announcementTitle")) if announcements else None,
    }
