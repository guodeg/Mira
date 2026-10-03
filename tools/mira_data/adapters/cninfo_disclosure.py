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
import io as _io
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
# The portal's full-text search index. It is a DIFFERENT index from the announcement feed
# above: 投资者关系活动记录表 is absent from `hisAnnouncement/query` for Shenzhen (and for the
# HK-line "海外监管公告" copies) but present here, which is why the IR channel needs both.
FULLTEXT_SEARCH_URL = "http://www.cninfo.com.cn/new/fulltextSearch/full"
# pageSize is silently capped at 100 regardless of what is sent, so asking for more only
# misreports the page count.
FULLTEXT_PAGE_SIZE = 100

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
    # 权益变动 is the one supply-change phrase the classifier was missing: 减持 and 增持 already
    # have rules above, but a title that only says 权益变动 (e.g. a 5%-threshold crossing after a
    # 询价转让) matched nothing and fell through to `other`.
    (r"权益变动", "equity_change"),
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


# --------------------------------------------------------------------------- #
# PDF body extraction (explicit, bounded, never silent)
# --------------------------------------------------------------------------- #

PDF_MAX_BYTES = 40 * 1024 * 1024
PDF_DEFAULT_MAX_PAGES = 40
# Reading a table inside a periodic report body needs a far larger page window than reading
# a leading section: 海光信息's 限售股份变动情况 sits on page 103 of a 231-page annual report, so
# the 40-page default truncates before it. The byte cap above stays the real safety limit.
PDF_SCAN_MAX_PAGES = 400


def _require_pypdf():
    try:
        import pypdf  # type: ignore
    except ImportError as exc:                       # pragma: no cover - depends on env
        raise net.FetchError(
            "cninfo_dependency_gap: install the optional dependency 'pypdf' to read "
            "announcement PDF bodies (no PDF parser is in the stdlib)") from exc
    return pypdf


def extract_pdf_text(
    url: str,
    *,
    max_pages: int = PDF_DEFAULT_MAX_PAGES,
    max_bytes: int = PDF_MAX_BYTES,
) -> dict:
    """Download one CNINFO PDF and return its text layer.

    Returns ``{"text", "page_count", "pages_read", "truncated", "chars", "url"}``.

    Raises ``net.FetchError`` with a machine-routable prefix when the body cannot be
    turned into text:

    - ``cninfo_dependency_gap``  — no PDF library installed.
    - ``cninfo_pdf_too_large``   — declared or actual size exceeds ``max_bytes``.
    - ``cninfo_pdf_unparsable``  — the bytes are not a readable PDF.
    - ``cninfo_pdf_text_gap``    — parsed fine but yielded no text on any page read
      (typically an image-only scan). **Never** returns empty text silently.
    """
    pypdf = _require_pypdf()
    raw = net.get(url, headers=HEADERS, timeout=_timeout())
    if len(raw) > max_bytes:
        raise net.FetchError(
            f"cninfo_pdf_too_large: {len(raw)} bytes exceeds the {max_bytes}-byte cap "
            f"for {url}; raise max_bytes deliberately rather than by accident")

    try:
        reader = pypdf.PdfReader(_io.BytesIO(raw))
        page_count = len(reader.pages)
    except Exception as exc:                         # noqa: BLE001 - vendor parser
        raise net.FetchError(f"cninfo_pdf_unparsable: {url}: {exc}") from exc

    pages_read = min(page_count, max_pages)
    chunks: list[str] = []
    for index in range(pages_read):
        try:
            chunks.append(reader.pages[index].extract_text() or "")
        except Exception as exc:                     # noqa: BLE001 - per-page recovery
            # One bad page must not discard the readable ones, but the loss is recorded.
            chunks.append(f"[[page {index + 1} extract failed: {exc}]]")

    text = "\n".join(chunks)
    if not text.strip():
        raise net.FetchError(
            f"cninfo_pdf_text_gap: {url} parsed to {page_count} pages but yielded no "
            "text layer on the first "
            f"{pages_read} page(s); this is an image-only scan, not an empty document")
    return {
        "text": text,
        "page_count": page_count,
        "pages_read": pages_read,
        "truncated": pages_read < page_count,
        "chars": len(text),
        "url": url,
    }


def find_announcements(
    symbol: str,
    *,
    since: Optional[str] = None,
    until: Optional[str] = None,
    title_contains: Optional[str] = None,
    max_items: Optional[int] = None,
) -> list[dict]:
    """Announcement metadata rows for one name, optionally filtered by title substring.

    A thin convenience over the same index query :func:`fetch_issuer_disclosures`
    uses, for the common "find the filing that discusses X, then extract it" flow.
    Returns the raw index dicts (with ``announcementTitle`` cleaned in place) so a
    caller can hand ``pdf_url(item['adjunctUrl'])`` straight to
    :func:`extract_pdf_text`.
    """
    thscode = resolve_thscode(symbol)
    sec_code, _board = thscode.split(".")
    org = resolve_org_id(sec_code)
    until = until or _dt.date.today().isoformat()
    since = since or (_dt.date.fromisoformat(until) - _dt.timedelta(days=DEFAULT_DAYS)).isoformat()

    rows: list[dict] = []
    for item in _iter_announcements(
        stock=f"{sec_code},{org}", se_date=f"{since}~{until}",
        max_items=_max_items(max_items),
    ):
        if title_contains and title_contains not in clean_title(item.get("announcementTitle")):
            continue
        item = dict(item)
        item["announcementTitle"] = clean_title(item.get("announcementTitle"))
        item["date_bj"] = bj_date(item.get("announcementTime"))
        item["pdf_url"] = pdf_url(item.get("adjunctUrl"))
        rows.append(item)
    return rows


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


# --------------------------------------------------------------------------- #
# 限售股份变动情况 — the one filing table that is safely recoverable
# --------------------------------------------------------------------------- #
#
# The L1 review listed three structured fields the plain text layer destroys. Two are now
# served elsewhere (股东户数 is lifted by regex; the 前十名股东 tables come from a relay), and
# this section covers the third: the issuer's per-holder restricted-share table.
#
# Which extractor, and why — measured, not assumed
# ------------------------------------------------
# Both pypdf modes were run against live filings before choosing:
#
# - **plain** (`extract_text()`) explodes the table into reading-order fragments — the header
#   arrives as `是否有 履行期 限` and numbers lose their column binding. Unusable.
# - **layout** (`extraction_mode="layout"`) keeps every table row on one line, and for *this*
#   table the cells are space-separated, so a row arrives as
#   `中科曙光 649,900,000 649,900,000 0 0 首发限售 2025/8/12`.
# - **coordinates** (`visitor_text` + clustering) were also prototyped. They do preserve cell
#   boundaries, but one visual row is emitted as *two* clusters ~0.6pt apart (the numeric
#   cells sit at y=454.6, the holder-name and reason cells at y=454.0), so any single global
#   row-band tolerance wide enough to merge them also merges the neighbouring row 15.7pt away.
#   Making that robust needs per-column y-grouping, which is more machinery than this table
#   repays — and the layout path below is validated by a checksum anyway.
#
# So layout mode it is, with three honesty rules that keep it safe:
#
# 1. **The table is located by its own header.** No `限售股份变动情况` / `年初限售股数`, no parse.
# 2. **Cells are recovered right-to-left.** The trailing columns (限售原因, 解除限售日期) are
#    unambiguous, and taking the numeric columns from the right avoids the one place layout
#    mode runs two cells together — `1,437,780,9101,437,780,910` in the 合计 row — which this
#    module never has to read, because the published total is used as a CHECK.
# 3. **The issuer's own 合计 row validates the parse.** If the holder rows do not sum to it,
#    the adapter refuses to emit rather than publishing a plausible-looking wrong table.

LOCKUP_UNIT = "shares"
# Thousands separators must be properly grouped. A looser `[\d,]+` accepts a GLUED pair as
# one valid number — `1,437,780,9101,437,780,910` parsed as 1.4e19 — which silently defeats
# both the checksum and the splitter that repairs it.
CELL_NUMBER_RE = re.compile(r"^-?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?$")
CELL_DATE_RE = re.compile(r"^\d{4}\s*[/\-年]\s*\d{1,2}\s*[/\-月]\s*\d{1,2}\s*日?$")
# 限售原因 vocabulary: 首发限售 / 股权激励 / 定向增发 / 战略配售 / 其他 …
LOCKUP_REASON_RE = re.compile(r"^[\u4e00-\u9fff]{2,12}$")
LOCKUP_REASON_TOKENS = ("首发", "限售", "激励", "增发", "配售", "锁定", "转让", "其他")


def _cell_number(value: str) -> Optional[float]:
    text = (value or "").strip()
    if not CELL_NUMBER_RE.match(text):
        return None
    try:
        return float(text.replace(",", "").replace("，", ""))
    except ValueError:
        return None


def _cell_date(value: str) -> str:
    text = re.sub(r"\s+", "", value or "")
    match = re.match(r"^(\d{4})[/\-年](\d{1,2})[/\-月](\d{1,2})日?$", text)
    if not match:
        return ""
    year, month, day = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def _lockup_reason(value: str) -> str:
    text = (value or "").strip()
    if not text or not LOCKUP_REASON_RE.match(text):
        return ""
    return text if any(token in text for token in LOCKUP_REASON_TOKENS) else ""


def _split_glued_numbers(blob: str, known: list[float]) -> list[float]:
    """Split a token that layout mode glued together out of two published numbers.

    Only the 合计 row does this (`1,437,780,9101,437,780,910`) because it is the one row whose
    cells sit flush. Ambiguity is resolved structurally, in order of strength:

    1. a split whose two halves are **equal** — the observed case, and self-evidencing;
    2. a split whose two halves are numbers the holder rows already published;
    3. a split whose *right* half is known, leaving the left as the remainder (the totals row
       publishes a single-column sum, so that sum will not appear in ``known``);
    4. a single surviving candidate.

    Anything still ambiguous returns nothing and is reported as a gap, because a wrong split
    here would silently corrupt the checksum the whole parser rests on.
    """
    def num(text: str) -> Optional[float]:
        # ``_cell_number`` validates the comma form FIRST (``CELL_NUMBER_RE``); stripping the
        # commas before the digit test instead would accept malformed halves like ``'1,'``
        # and ``',437'``, which is what turned this token into 14 bogus candidates.
        return _cell_number(text)

    def is_known(value: float) -> bool:
        return any(abs(value - k) < 0.5 for k in known)

    candidates: list[list[float]] = []
    for index in range(1, len(blob)):
        left, right = num(blob[:index]), num(blob[index:])
        if left is None or right is None:
            continue
        candidates.append([left, right])
    if not candidates:
        return []
    equal = [pair for pair in candidates if abs(pair[0] - pair[1]) < 0.5]
    if len(equal) == 1:
        return equal[0]
    both_known = [pair for pair in candidates if all(is_known(v) for v in pair)]
    if len(both_known) == 1:
        return both_known[0]
    right_known = [pair for pair in candidates if is_known(pair[1])]
    if len(right_known) == 1:
        return right_known[0]
    return candidates[0] if len(candidates) == 1 else []


def parse_lockup_change_rows(rows: list[list[str]]) -> dict:
    """Parse already-split rows of a 限售股份变动情况 table.

    An accepted row carries four numbers — 年初限售股数, 本年解除限售股数, 本年增加限售股数,
    年末限售股数 — optionally followed by a 限售原因 and a 解除限售日期. Anything else is
    reported as a gap rather than guessed at.

    The published 合计 row is a **check, never a claim**: the working invariant is
    ``年末 = 年初 - 本年解除 + 本年增加`` per holder, and the holders' columns must sum to the
    published total. A mismatch is surfaced, never averaged away.
    """
    holders: list[dict] = []
    rollup_raw: Optional[list[str]] = None
    gaps: list[str] = []

    for cells in rows:
        tokens = [c for c in ((c or "").strip() for c in cells) if c]
        if len(tokens) < 5:
            continue
        name = tokens[0]
        if _cell_number(name) is not None or _cell_date(name):
            continue
        if name in {"合计", "总计"}:
            rollup_raw = tokens
            continue
        date = next((d for d in (_cell_date(t) for t in tokens) if d), "")
        reason = next((t for t in tokens if _lockup_reason(t)), "")
        # Column order is as published, left to right.
        operands = [t for t in tokens[1:] if _cell_number(t) is not None and t != reason]
        if len(operands) != 4:
            gaps.append(f"{name}: expected 4 numeric columns, found {len(operands)} ({tokens})")
            continue
        opening, released, added, closing = (_cell_number(v) for v in operands)
        holders.append({
            "holder": name, "opening": opening, "released": released,
            "added": added, "closing": closing, "reason": reason, "date": date,
        })

    # The 合计 row is the checksum, so its own glued token has to be repaired before use.
    rollup: Optional[dict] = None
    if rollup_raw is not None:
        known = [h[k] for h in holders for k in ("opening", "released", "added", "closing")]
        operands: list[float] = []
        for token in rollup_raw[1:]:
            value = _cell_number(token)
            if value is not None:
                operands.append(value)
            elif re.fullmatch(r"[\d,]+", token):
                # A glued pair of numbers, e.g. `1,437,780,9101,437,780,910`. This is the FIRST
                # numeric cell after 合计, so it must not be gated on `operands` being non-empty
                # - doing that silently skipped the repair and dropped the checksum. Separator
                # cells (`/`, `-`, `不适用`) fail the digits-and-commas test.
                operands.extend(_split_glued_numbers(token, known))
        if len(operands) >= 4:
            rollup = dict(zip(("opening", "released", "added", "closing"), operands))
        else:
            gaps.append(f"合计 row could not be split into four columns: {rollup_raw}")

    checks: dict = {}
    if rollup is not None:
        for label, key in (("Opening", "opening"), ("Released", "released"),
                           ("Added", "added"), ("Closing", "closing")):
            checks[f"rollup{label}MatchesSum"] = (
                abs(sum(h[key] for h in holders) - rollup[key]) < 0.5)
    for holder in holders:
        expected = holder["opening"] - holder["released"] + holder["added"]
        if abs(expected - holder["closing"]) >= 0.5:
            gaps.append(f"{holder['holder']}: 年初 {holder['opening']:,.0f} - "
                        f"解除 {holder['released']:,.0f} + 增加 {holder['added']:,.0f} != "
                        f"年末 {holder['closing']:,.0f}")
    return {"holders": holders, "rollup": rollup, "checks": checks, "gaps": gaps}


def lockup_table_lines(layout_text: str) -> list[list[str]]:
    """Data rows of the 限售股份变动情况 table, taken from a layout-mode page body.

    The header is the anchor; the search starts at the section title so a page carrying
    several tables cannot hand back the wrong one, and stops at the next numbered section.
    """
    lines = layout_text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if "限售股份变动情况" in line:
            start = index
        if start is not None and "年初限售股数" in line:
            start = index
            break
    if start is None:
        return []
    rows: list[list[str]] = []
    for line in lines[start + 1:]:
        tokens = line.split()
        if not tokens:
            continue
        if any(token.startswith(("二、", "三、", "四、", "五、", "六、", "七、"))
               for token in tokens):
            break
        first = tokens[0]
        if _cell_number(first) is not None or _cell_date(first):
            continue
        if not (5 <= len(tokens) <= 9):
            continue
        rows.append(tokens)
    return rows


def fetch_lockup_change(
    symbol: str,
    *,
    as_of: Optional[str] = None,
    market_scope: str = "CN",
    since: Optional[str] = None,
    until: Optional[str] = None,
    max_pages: int = PDF_SCAN_MAX_PAGES,
    max_items: Optional[int] = None,
) -> FetchResult:
    """限售股份变动情况 for one name, read from the newest periodic report body (L1).

    This is the per-holder restricted-share movement the A-share market-structure gate asks
    for: who still holds locked shares, how many were released, how many were added and what
    remains, with the 限售原因 and 解除限售日期 that travel with it. The standalone
    限售股上市流通公告 covers one unlock event at a time; this reads the periodic report's
    table, which is the one the L1 review named.

    Refuses loudly rather than emitting a dubious table: a body with no such table, or one
    whose rows do not sum to the issuer's own 合计, raises a routable gap instead.
    """
    as_of = as_of or _dt.date.today().isoformat()
    thscode = resolve_thscode(symbol)
    sec_code, _board = thscode.split(".")
    until = until or as_of
    since = since or (_dt.date.fromisoformat(until) - _dt.timedelta(days=730)).isoformat()

    # ``半年度报告`` CONTAINS the substring ``年度报告``, so a naive "年度报告 in title" test
    # classifies every interim report as an annual one — the same trap report_period_end
    # documents. The interim test therefore excludes the annual marker.
    reports = [item for item in find_announcements(
        symbol, since=since, until=until, max_items=max_items)
        if "摘要" not in item["announcementTitle"]
        # The portal also publishes translated bodies (e.g. `2025年年度报告（英文版）`). Their
        # tables are laid out differently and translate the column labels, so the Chinese
        # original is the one this parser reads.
        and "英文" not in item["announcementTitle"]
        and "english" not in item["announcementTitle"].lower()
        and ("半年度报告" in item["announcementTitle"]
             or "年度报告" in item["announcementTitle"])]
    if not reports:
        raise net.FetchError(
            f"cninfo_source_gap: no periodic report body for {thscode} between {since} and {until}")

    # Prefer the annual report: an issuer can mark the table 适用 in one periodic report and
    # 不适用 in the next (measured on 海光信息, whose FY2025 annual report carries the table
    # while its H1 2026 report does not), so "newest body" alone reports a false gap.
    annual = [item for item in reports if "半年度报告" not in item["announcementTitle"]]
    report = sorted(annual or reports, key=lambda i: i.get("date_bj") or "")[-1]

    pypdf = _require_pypdf()
    raw = net.get(report["pdf_url"], headers=HEADERS, timeout=_timeout())
    if len(raw) > PDF_MAX_BYTES:
        raise net.FetchError(
            f"cninfo_pdf_too_large: {len(raw)} bytes exceeds the {PDF_MAX_BYTES}-byte cap "
            f"for {report['pdf_url']}")
    try:
        reader = pypdf.PdfReader(_io.BytesIO(raw))
    except Exception as exc:                             # noqa: BLE001 - vendor parser
        raise net.FetchError(f"cninfo_pdf_unparsable: {report['pdf_url']}: {exc}") from exc

    period = report_period_end(report["announcementTitle"]) or (report.get("date_bj") or as_of)
    posture = POSTURES["cninfo_lockup_change"]
    records: list[CanonicalRecord] = []
    seen_holders: set[str] = set()

    for index in range(min(len(reader.pages), max_pages)):
        try:
            layout = reader.pages[index].extract_text(extraction_mode="layout") or ""
        except Exception:                                # noqa: BLE001 - per-page recovery
            continue
        if "限售股份变动情况" not in layout or "年初限售股数" not in layout:
            continue
        parsed = parse_lockup_change_rows(lockup_table_lines(layout))
        if not parsed["holders"]:
            continue
        # The checksum is MANDATORY, not best-effort. An earlier draft emitted whenever
        # `checks` was empty, which meant a table whose 合计 row failed to parse was
        # published with no validation at all - and it was wrong (the columns were read
        # backwards) while looking perfectly plausible. No verified rollup, no emission.
        if not parsed["checks"]:
            raise net.FetchError(
                f"cninfo_lockup_check_missing: {thscode} {report['announcementTitle']} page "
                f"{index + 1}: the 合计 row could not be parsed, so the holder rows cannot be "
                f"validated ({parsed['gaps']}); refusing to emit an unverified table")
        failed = {k: v for k, v in parsed["checks"].items() if v is False}
        if failed:
            raise net.FetchError(
                f"cninfo_lockup_check_failed: {thscode} {report['announcementTitle']} page "
                f"{index + 1}: holder rows do not sum to the issuer's published 合计 row "
                f"({failed}); refusing to emit a table that does not reconcile")
        for holder in parsed["holders"]:
            if holder["holder"] in seen_holders:
                continue
            seen_holders.add(holder["holder"])
            common = {
                "holderName": holder["holder"],
                "lockupReason": holder["reason"] or "未标注限售原因",
                "unlockDate": holder["date"] or None,
                "reportTitle": report["announcementTitle"],
                "reportUrl": report["pdf_url"],
                "reportDate": report.get("date_bj"),
                "pdfPage": index + 1,
                "rollupCheck": parsed["checks"],
                "rowGaps": parsed["gaps"],
                "extraction": ("cells taken from the layout-mode text layer, right to left, "
                               "and validated against the issuer's own 合计 row"),
                "upgradePath": ("the same unlock is filed per event in a standalone "
                                "限售股上市流通公告; that filing is the cross-check"),
            }
            for metric, key, label in (
                ("lockup_opening_balance", "opening", "年初限售股数"),
                ("lockup_released", "released", "本年解除限售股数"),
                ("lockup_added", "added", "本年增加限售股数"),
                ("lockup_closing_balance", "closing", "年末限售股数"),
            ):
                value = holder[key]
                when = f"（解除限售日期 {holder['date']}）" if holder["date"] else ""
                records.append(CanonicalRecord(
                    family="ownership_short_interest", research_object=thscode,
                    market_scope=market_scope, metric=metric, value=float(value),
                    unit=LOCKUP_UNIT, period=period, period_type="fiscal_period",
                    as_of_date=as_of, source_date=report.get("date_bj") or as_of,
                    posture=posture, url_or_path=report["pdf_url"],
                    claim_text=f"{thscode} {period} {holder['holder']} {label} {value:,.0f} 股{when}",
                    provenance=dict(common),
                ))

    if not records:
        raise net.FetchError(
            f"cninfo_lockup_not_applicable: {thscode} {report['announcementTitle']} — no page "
            f"carried a 限售股份变动情况 table with a 年初限售股数 header; the issuer reports no "
            f"restricted-share movement for {period}")
    return FetchResult(records)


# --------------------------------------------------------------------------- #
# Shareholder count lifted out of the filed report body
# --------------------------------------------------------------------------- #

# Verified against a live filing (贵州茅台 2026 半年度报告, 110 pages): the label and the figure
# come out of the PDF text layer adjacent, as
#     (一) 股东总数： 截至报告期末普通股股东总数(户) 296,404
# which is why exactly this field is extracted and the shareholder *tables* are not.
SHAREHOLDER_COUNT_PATTERNS = (
    r"截至报告期末普通股股东总数\s*[（(]?\s*户\s*[)）]?\s*[:：]?\s*([\d,，]+)",
    r"股东总数\s*[（(]\s*户\s*[)）]\s*[:：]?\s*([\d,，]+)",
    r"普通股股东总数\s*[:：]?\s*([\d,，]+)\s*户",
)
SHAREHOLDER_TABLE_CAVEAT = (
    "only the shareholder count is extracted: the top-ten shareholder and 限售 tables lose their "
    "column binding in the PDF text layer (headers split as '期末持股数 量', values in reading "
    "order), so they are not claimed from text rather than guessed; read the report PDF itself")
# Order matters: 半年度报告 contains the substring 年度报告, so the more specific markers must
# be tested first or a half-year report would be dated as a year end.
REPORT_PERIODS = (("半年度报告", "-06-30"), ("第一季度报告", "-03-31"),
                  ("第三季度报告", "-09-30"), ("年度报告", "-12-31"))


def report_period_end(title: str) -> str:
    """Period end implied by a periodic-report title, or '' when the title does not say."""
    text = clean_title(title)
    year = re.search(r"(20\d{2})\s*年", text)
    if not year or "摘要" in text:
        return ""
    for marker, suffix in REPORT_PERIODS:
        if marker in text:
            return f"{year.group(1)}{suffix}"
    return ""


def _shareholder_count(text: str) -> tuple[float, str, int]:
    for pattern in SHAREHOLDER_COUNT_PATTERNS:
        match = re.search(pattern, text)
        if not match:
            continue
        raw = match.group(1).replace(",", "").replace("，", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        if value <= 0:
            continue
        start = max(0, match.start() - 60)
        return value, re.sub(r"\s+", " ", text[start:match.end()]).strip(), match.start()
    return 0.0, "", -1


def fetch_shareholder_count(
    symbol: str,
    *,
    as_of: Optional[str] = None,
    market_scope: str = "CN",
    max_pages: int = PDF_DEFAULT_MAX_PAGES,
) -> FetchResult:
    """Shareholder count from the most recent periodic report, with its verbatim source line.

    The claim is deliberately narrow: one figure the filing states outright, with the sentence
    it came from kept in provenance so a reader can check it, and an explicit note that the
    shareholder tables are *not* extracted from the text layer.
    """
    as_of = as_of or _dt.date.today().isoformat()
    thscode = resolve_thscode(symbol)
    candidates: list[dict] = []
    for marker in ("年度报告", "半年度报告", "季度报告"):
        try:
            candidates.extend(find_announcements(symbol, until=as_of, title_contains=marker,
                                                 max_items=12))
        except net.FetchError:
            continue
    reports = [row for row in candidates
               if "摘要" not in row["announcementTitle"] and "英文" not in row["announcementTitle"]]
    if not reports:
        raise net.FetchError(
            f"cninfo_source_gap: no periodic report found for {thscode} up to {as_of}")
    reports.sort(key=lambda row: row["date_bj"], reverse=True)

    posture = POSTURES["cninfo_holders"]
    errors: list[str] = []
    for report in reports[:3]:
        try:
            got = extract_pdf_text(report["pdf_url"], max_pages=max_pages)
        except net.FetchError as exc:
            errors.append(f"{report['announcementTitle']}: {exc}")
            continue
        found = _shareholder_count(got["text"])
        if not found[1]:
            errors.append(f"{report['announcementTitle']}: shareholder total not stated")
            continue
        value, sentence, offset = found
        period = report_period_end(report["announcementTitle"])
        return FetchResult([CanonicalRecord(
            family="ownership_short_interest", research_object=thscode,
            market_scope=market_scope, metric="shareholder_count", value=value,
            unit="households", period=period or report["date_bj"],
            period_type="point_in_time", as_of_date=as_of, source_date=report["date_bj"],
            posture=posture, url_or_path=report["pdf_url"],
            claim_text=(f"{thscode} {report['announcementTitle']} 披露的普通股股东总数 "
                        f"{int(value):,} 户" + (f"（{period}）" if period else "")),
            provenance={
                "reportTitle": report["announcementTitle"],
                "reportDisclosedOn": report["date_bj"], "reportUrl": report["pdf_url"],
                "periodBasis": ("derived from the report title" if period else
                                "not derivable from the title; the disclosure date is used"),
                "matchedSentence": sentence[:200], "charOffset": offset,
                "pagesRead": got["pages_read"], "pageCount": got["page_count"],
                "extractionMethod": "pdf_text_layer_regex/v1",
                "tableCaveat": SHAREHOLDER_TABLE_CAVEAT,
                "bodyRetrieval": ("filed body parsed locally; only the figure and this sentence "
                                  "are retained"),
            },
        )])
    raise net.FetchError(
        "cninfo_source_gap: no shareholder total reachable in the last three periodic reports; "
        + "; ".join(errors[:3]))


# --------------------------------------------------------------------------- #
# 投资者关系活动记录表 — the filed transcript of an IR activity / 业绩说明会
# --------------------------------------------------------------------------- #

# Verified against a live filing (宁波精达 2026 年半年度业绩说明会, 4 pages). The form prints its
# labels **without colons** ("时间 2026 年 9 月 29 日"), the activity category as a checkbox list
# ("□特定对象调研 ☑业绩说明会 …"), and the Q&A as numbered questions each followed by 答：.
IR_ACTIVITY_TITLE = "投资者关系活动记录表"
IR_CATEGORIES = ("特定对象调研", "分析师会议", "媒体采访", "业绩说明会", "新闻发布会",
                 "路演活动", "现场参观", "其他")
IR_ANSWER_LIMIT = 200
IR_QUESTION_LIMIT = 120
IR_LABEL_PATTERNS = {
    "number": r"编\s*号\s*[:：]\s*([0-9A-Za-z\-—]+)",
    "date": r"时\s*间\s*[:：]?\s*(20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)",
    "venue": r"地\s*点\s*[:：]?\s*([^\n]{2,60})",
    "participants": r"参与单位名称及?\s*人员姓名\s*([^\n]{2,120})",
    "receptionists": r"上市公司接待人\s*员姓名\s*([^\n]{2,160})",
}
IR_NOT_EXTRACTED = ("free-text colour (tone, hedging, evasion) is not extracted; only the "
                    "filed wording is quoted")


def search_announcements_fulltext(
    search_key: str,
    *,
    since: Optional[str] = None,
    until: Optional[str] = None,
    title_contains: Optional[str] = None,
    max_pages: int = 3,
) -> list[dict]:
    """Documents matching ``search_key`` from the portal's full-text search index.

    **Why this exists next to ``find_announcements``.** The announcement feed
    (``hisAnnouncement/query``) and this index are separate: 投资者关系活动记录表 is filed for
    Shenzhen names and for the HK-line "海外监管公告" copies, but is simply absent from the feed,
    so a feed-only lookup reports a false gap. Measured 2026-10-03: the feed returns 17 rows for
    亿道信息 in 2026-09 with none of them an IR record, while this index returns ~1020 IR records
    market-wide going back to 2018-03, including Shenzhen codes (301277, 002486, 002921, …).

    Contract measured live:

    - ``POST`` form-encoded; the keyword field is ``searchkey`` and it **is** honoured — a
      nonsense key returns ``total`` 0 while a real one returns the corpus size.
    - ``pageSize`` is **silently capped at 100**; a larger request is accepted and ignored, and
      the reported ``totalpages`` shrinks to match the cap, so the cap is applied here.
    - ``sdate``/``edate`` (``YYYY-MM-DD``) filter the result set, but ``total`` then counts only
      the window, so an empty keyword with a window is a usable date sweep of the whole corpus.
    - Rows carry the same identity fields as the feed (``secCode``, ``announcementTitle``,
      ``announcementTime`` as a Beijing epoch, ``adjunctUrl``), so the same PDF route and the
      same parser apply.
    - The index also returns **5-digit HK codes** for A+H issuers, which are not A-share symbols
      and must be filtered by the caller rather than silently accepted.
    """
    since = since or ""
    until = until or ""
    rows: list[dict] = []
    for page in range(1, max(1, max_pages) + 1):
        payload = net.post_form_json(FULLTEXT_SEARCH_URL, {
            "searchkey": search_key, "sdate": since, "edate": until,
            "isfulltext": "false", "sortName": "nothing", "sortType": "desc",
            "pageNum": str(page), "pageSize": str(FULLTEXT_PAGE_SIZE),
        }, headers=HEADERS, retries=2, backoff=1.5)
        batch = payload.get("announcements") or []
        for item in batch:
            item = dict(item)
            item["announcementTitle"] = clean_title(item.get("announcementTitle"))
            item["date_bj"] = bj_date(item.get("announcementTime"))
            item["pdf_url"] = pdf_url(item.get("adjunctUrl"))
            if title_contains and title_contains not in item["announcementTitle"]:
                continue
            rows.append(item)
        if len(batch) < FULLTEXT_PAGE_SIZE:
            break
    return rows


def _ir_field(text: str, pattern: str) -> str:
    match = re.search(pattern, text)
    return re.sub(r"\s+", " ", match.group(1)).strip(" ：:") if match else ""


def _ir_iso_date(value: str) -> str:
    """``2026 年 3 月 31 日`` -> ``2026-03-31``; the form prints dates in Chinese."""
    match = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", value or "")
    if not match:
        return ""
    year, month, day = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def parse_ir_activity(text: str) -> dict:
    """Pull the activity header and the numbered Q&A out of a filed IR activity record."""
    squashed = re.sub(r"[ \t]+", " ", text)
    header = {name: _ir_field(squashed, pattern)
              for name, pattern in IR_LABEL_PATTERNS.items()}
    checkbox = re.search(r"([☑√])\s*(" + "|".join(IR_CATEGORIES) + ")", squashed)
    header["category"] = checkbox.group(2) if checkbox else ""
    header["categories_all"] = [name for name in IR_CATEGORIES
                                if re.search(r"[☑√]\s*" + name, squashed)]

    # Questions are numbered; every answer starts with 答：. Split on the answer marker and take
    # the last numbered item before it as the question.
    pairs: list[tuple[str, str]] = []
    chunks = squashed.split("答：")
    for index in range(1, len(chunks)):
        answer = re.split(r"\n\s*\d+[\.、]", chunks[index])[0].strip()
        prompt = chunks[index - 1]
        numbered = re.findall(r"(?:^|\n)\s*\d+[\.、]\s*([^\n]{4,})", prompt)
        question = (numbered[-1] if numbered else prompt.strip().split("\n")[-1]).strip()
        if not answer or not question:
            continue
        pairs.append((re.sub(r"\s+", " ", question), re.sub(r"\s+", " ", answer)))
    header["qa_pairs"] = pairs
    return header


def fetch_ir_activity(
    symbol: str,
    *,
    since: Optional[str] = None,
    until: Optional[str] = None,
    max_items: Optional[int] = None,
    as_of: Optional[str] = None,
    market_scope: str = "CN",
    max_pages: int = PDF_DEFAULT_MAX_PAGES,
) -> FetchResult:
    """Filed IR activity records (调研 / 业绩说明会) as ``transcript_claim`` rows.

    One record per answered question, because the claim is what the company *said*, while the
    activity header (category, participants, date, venue, receptionists) travels in provenance
    on every record. Texts are extracts, never full copies, and nothing is inferred from tone.

    Covers both markets through two indexes, feed first and full-text search as the fallback:
    Shanghai filers publish the record as an ordinary announcement, while Shenzhen filers and
    the HK-line "海外监管公告" copies are absent from the announcement feed and only appear in the
    search index. An earlier revision of this adapter concluded Shenzhen was unreachable; that
    was an artefact of querying the feed alone.
    """
    as_of = as_of or _dt.date.today().isoformat()
    thscode = resolve_thscode(symbol)
    sec_code, _board = thscode.split(".")
    cap = _max_items(max_items)
    until = until or as_of
    since = since or (_dt.date.fromisoformat(until) - _dt.timedelta(days=DEFAULT_DAYS)).isoformat()
    # The title filter runs after a bounded index read, so ask for a wide page and filter here:
    # a small cap would silently never reach the record (this bit the first probe).
    rows = [row for row in find_announcements(symbol, until=until, max_items=max(cap * 10, 100))
            if IR_ACTIVITY_TITLE in row["announcementTitle"]
            and row["date_bj"] >= since]
    source_index = "announcement_feed"
    if not rows:
        # Fallback index. Filter to the requested A-share code: the same search also returns
        # the issuer's 5-digit HK-line copies, and those are a different security. Three pages
        # of 100 is a bounded sweep - one name's IR history is a handful of records, not a
        # market-wide backfill.
        rows = [row for row in search_announcements_fulltext(
            sec_code, since=since, until=until, title_contains=IR_ACTIVITY_TITLE, max_pages=3)
            if str(row.get("secCode") or "").strip() == sec_code
            and row["date_bj"] >= since]
        source_index = "fulltext_search"
    if not rows:
        raise net.FetchError(
            f"cninfo_source_gap: no 投资者关系活动记录表 for {thscode} between {since} and "
            f"{until}; checked both the announcement feed and the full-text search index")

    posture = POSTURES["cninfo_ir_activity"]
    errors: list[str] = []
    for row in sorted(rows, key=lambda item: item["date_bj"], reverse=True):
        try:
            got = extract_pdf_text(row["pdf_url"], max_pages=max_pages)
        except net.FetchError as exc:
            errors.append(f"{row['announcementTitle']}: {exc}")
            continue
        parsed = parse_ir_activity(got["text"])
        pairs = parsed.get("qa_pairs") or []
        if not pairs:
            errors.append(f"{row['announcementTitle']}: no numbered Q&A in the text layer")
            continue
        records = []
        # `period` stays ISO-8601 like every other adapter emits; the verbatim Chinese form
        # date is kept alongside it in provenance as `activityDate`.
        activity_iso = _ir_iso_date(parsed.get("date") or "") or row["date_bj"]
        for index, (question, answer) in enumerate(pairs[:cap], start=1):
            records.append(CanonicalRecord(
                family="transcript_claim", research_object=thscode,
                market_scope=market_scope, metric="ir_qa_answer",
                value=f"{row.get('announcementId') or row['date_bj']}-{index}",
                unit="qa_id", period=activity_iso,
                period_type="point_in_time", as_of_date=as_of, source_date=row["date_bj"],
                posture=posture, url_or_path=row["pdf_url"],
                claim_text=(f"{thscode} {activity_iso} "
                            f"{parsed.get('category') or '投资者关系活动'} 问答："
                            f"{answer[:IR_ANSWER_LIMIT]}"),
                provenance={
                    "filingTitle": row["announcementTitle"], "filingUrl": row["pdf_url"],
                    "activityNumber": parsed.get("number"), "activityDate": parsed.get("date"),
                    "activityCategory": parsed.get("category"),
                    "categoriesAll": parsed.get("categories_all"),
                    "participants": parsed.get("participants"),
                    "venue": parsed.get("venue"),
                    "receptionists": parsed.get("receptionists"),
                    "question": question[:IR_QUESTION_LIMIT],
                    "answerChars": len(answer), "qaIndex": index, "qaCount": len(pairs),
                    "pagesRead": got["pages_read"], "pageCount": got["page_count"],
                    "extractionMethod": "pdf_text_layer_form/v1",
                    "sourceIndex": source_index,
                    "notExtracted": IR_NOT_EXTRACTED,
                    "bodyRetrieval": ("filed body parsed locally; the question and a capped "
                                      "answer extract are retained"),
                },
            ))
        if records:
            return FetchResult(records)
    raise net.FetchError(
        "cninfo_source_gap: no readable 投资者关系活动记录表 for " + thscode + "; "
        + "; ".join(errors[:3]))
