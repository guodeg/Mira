"""Investor Q&A adapters (互动易 / 上证e互动) -> canonical ``transcript_claim``.

Fills the L4 company-voice channel: what the issuer itself says when investors ask,
on the exchange-designated platforms. Tier is deliberately split from claim type —
the **channel** is an official exchange platform (``regulatory_and_exchange``, L2)
while the **content** is company commentary (``company_claim`` /
``company_statement``), because ``data/claim-taxonomy.md`` says a company claim
"只能作为公司口径，需交叉验证" and must not be written up as a verified fact.

Coverage is split by exchange, and that is a fact about the sources, not a choice:
**互动易 (irm.cninfo.com.cn) is the Shenzhen platform** — probed live on 2026-10-02,
000001 returned four pages of Q&A while 600519 and 688111 both returned zero rows — so
Shanghai names come from **上证e互动 (sns.sseinfo.com)**. Beijing-exchange names have
neither, and degrade to a labelled source gap.

Verified contracts (probed live 2026-10-02)
-------------------------------------------
互动易, two POSTs, JSON:
- ``/newircs/index/queryKeyboardInfo`` with ``keyWord=<code>`` -> ``data[0].secid``
  (already in CNINFO's orgId shape: ``gssz0000001`` / ``gssh0600519``)
- ``/newircs/company/question`` with ``stockcode``, ``orgId``, ``pageSize``, ``pageNum``
  -> ``{totalPage, rows:[{indexId, mainContent, attachedContent, attachedAuthor,
  authorName, pubDate, attachedPubDate, ...}]}``. ``pubDate`` is an epoch in **UTC ms**
  and must be read in Asia/Shanghai (the same off-by-one trap as the other CN sources).
  Rows carry the reply inline, so one read costs two requests.

上证e互动:
- ``POST /allcompany.do`` (``code=0&order=2&areaId=0&page=N``) returns HTML with 32
  companies per page, **sorted by code ascending** (page 1: 600000-600039, page 40:
  603334-603378, page 73: 688808-900948), so the uid for one code is found by binary
  searching the page range instead of pulling all 73 pages. The markup is
  single-quoted with an unquoted uid (``uid=65``), which is why a naive regex for
  ``uid="65"`` finds nothing.
- ``POST /ajax/userfeeds.do`` (``typeCode=company&type=11&pageSize=100&uid=<uid>&page=N``)
  returns an **HTML fragment**, not JSON: one ``div.m_feed_item`` per Q&A pair, with
  ``div.m_feed_txt`` holding question then answer, ``div.m_feed_from`` the dates, and
  ``a[rel=face]`` the questioner. Parsed here with the stdlib ``html.parser``.

What is deliberately **not** emitted: unanswered questions. They are a real signal of
investor attention, but they are not company speech, so labelling them
``company_claim`` would be wrong and dressing them up under another posture is a
separate design decision. Only answered pairs become claims, and every claim keeps the
question for context plus the citable platform URL for the full text (extracting a
claim instead of copying long text, per ``data/ingestion-layer.md``).
"""

from __future__ import annotations

import datetime as _dt
import re
import urllib.parse
from html.parser import HTMLParser
from typing import Optional

from .. import config, net
from ..canonical import POSTURES, CanonicalRecord, FetchResult
from .hithink_finance import resolve_thscode  # shared A-share symbol -> thscode map

IRM_ORGID_URL = "https://irm.cninfo.com.cn/newircs/index/queryKeyboardInfo"
IRM_QUESTION_URL = "https://irm.cninfo.com.cn/newircs/company/question"
IRM_DETAIL_URL = "https://irm.cninfo.com.cn/ircs/question/questionDetail?questionId={qid}"
SSE_COMPANY_URL = "https://sns.sseinfo.com/allcompany.do"
SSE_FEEDS_URL = "https://sns.sseinfo.com/ajax/userfeeds.do"
SSE_COMPANY_PAGE = "https://sns.sseinfo.com/company.do?uid={uid}"
IRM_ENDPOINT = "irm-cninfo://company/question/{thscode}"
SSE_ENDPOINT = "sse-einteraction://userfeeds/{thscode}"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
IRM_HEADERS = {"User-Agent": UA, "Referer": "https://irm.cninfo.com.cn/"}
SSE_HEADERS = {"User-Agent": UA, "Referer": "https://sns.sseinfo.com/"}
CN_TZ = _dt.timezone(_dt.timedelta(hours=8))

DEFAULT_MAX_ITEMS = 40
SSE_COMPANIES_PER_PAGE = 32
CLAIM_TEXT_LIMIT = 200
QUESTION_LIMIT = 120
_SSE_COMPANY_BLOCK = re.compile(r"company/(\d{6})\.png")
_SSE_UID = re.compile(r"uid=(\d+)")


def _setting(name: str, default: str) -> str:
    return (config.get(name) or default).strip()


def _max_items(limit: Optional[int]) -> int:
    if limit is not None:
        return max(1, int(limit))
    try:
        return max(1, int(_setting("MIRA_QA_MAX_ITEMS", str(DEFAULT_MAX_ITEMS))))
    except ValueError:
        return DEFAULT_MAX_ITEMS


def _max_uid_pages() -> int:
    try:
        return max(2, int(_setting("MIRA_QA_MAX_UID_PAGES", "10")))
    except ValueError:
        return 10


def _max_feed_pages() -> int:
    try:
        return max(1, int(_setting("MIRA_QA_MAX_FEED_PAGES", "3")))
    except ValueError:
        return 3


# --------------------------------------------------------------------------- #
# fetcher
# --------------------------------------------------------------------------- #

def fetch_investor_qa(
    symbol: str,
    *,
    as_of: Optional[str] = None,
    market_scope: str = "CN",
    days: Optional[int] = None,
    max_items: Optional[int] = None,
) -> FetchResult:
    """Answered investor Q&A for one A-share name, newest first."""
    as_of = as_of or _dt.date.today().isoformat()
    thscode = resolve_thscode(symbol)
    code, board = thscode.split(".")
    limit = _max_items(max_items)
    if board == "SZ":
        records = _irm_records(thscode, code, as_of=as_of, market_scope=market_scope, limit=limit)
    elif board == "SH":
        records = _sse_records(thscode, code, as_of=as_of, market_scope=market_scope, limit=limit)
    else:
        raise net.FetchError(
            f"investor_qa_source_gap: no investor-Q&A platform for {thscode}; 互动易 covers "
            "Shenzhen and 上证e互动 covers Shanghai, neither covers the Beijing exchange")

    if days:
        cutoff = (_dt.date.fromisoformat(as_of) - _dt.timedelta(days=days)).isoformat()
        records = [r for r in records if r.source_date >= cutoff]
    if not records:
        raise net.FetchError(
            f"investor_qa_source_gap: no answered investor questions for {thscode} — the "
            "platform returned an empty feed or only unanswered questions in the scanned "
            "pages. Companies with no activity on the platform (observed for some large "
            "caps) return an empty feed rather than an error upstream.")
    records.sort(key=lambda r: r.source_date, reverse=True)
    return FetchResult(records[:limit])


def _record(*, thscode: str, market_scope: str, as_of: str, posture, url: str,
            qa_id: str, question: str, answer: str, questioner: str,
            question_date: str, answer_date: str, platform: str) -> CanonicalRecord:
    answer = _squash(answer)
    question = _squash(question)
    return CanonicalRecord(
        family="transcript_claim", research_object=thscode, market_scope=market_scope,
        metric="investor_qa_answer", value=qa_id, unit="qa_id",
        period=answer_date or question_date, period_type="point_in_time",
        as_of_date=as_of, source_date=answer_date or question_date,
        posture=posture, url_or_path=url,
        claim_text=(f"{thscode} 回复投资者提问（{platform}）: "
                    f"{answer[:CLAIM_TEXT_LIMIT]}"),
        provenance={
            "platform": platform,
            "qaId": qa_id,
            "questioner": questioner,
            "questionDate": question_date,
            "answerDate": answer_date,
            "question": question[:QUESTION_LIMIT],
            "answerChars": len(answer),
            "fullTextAt": url,
        },
    )


# --------------------------------------------------------------------------- #
# 互动易 (Shenzhen)
# --------------------------------------------------------------------------- #

def _irm_org_id(code: str) -> str:
    payload = net.post_form_json(
        IRM_ORGID_URL + "?_t=0", {"keyWord": code}, headers=IRM_HEADERS)
    rows = payload.get("data") or []
    for row in rows:
        if str(row.get("secid") or "").strip():
            return str(row["secid"]).strip()
    raise net.FetchError(f"investor_qa_source_gap: 互动易 has no company id for {code}")


def _irm_records(thscode: str, code: str, *, as_of: str, market_scope: str,
                 limit: int) -> list[CanonicalRecord]:
    posture = POSTURES["irm_cninfo"]
    org_id = _irm_org_id(code)
    query = urllib.parse.urlencode({
        "_t": "0", "stockcode": code, "orgId": org_id,
        "pageSize": str(min(1000, max(limit * 4, 40))), "pageNum": "1",
        "keyWord": "", "startDay": "", "endDay": "",
    })
    payload = net.post_form_json(f"{IRM_QUESTION_URL}?{query}", {}, headers=IRM_HEADERS)
    rows = payload.get("rows") or []
    if not rows:
        raise net.FetchError(f"investor_qa_source_gap: 互动易 returned no questions for {code}")

    records = []
    for row in rows:
        answer = row.get("attachedContent")
        if not answer or not str(answer).strip():
            continue      # unanswered questions are not company speech
        qa_id = str(row.get("indexId") or row.get("attachedId") or "")
        if not qa_id:
            continue
        records.append(_record(
            thscode=thscode, market_scope=market_scope, as_of=as_of, posture=posture,
            url=IRM_DETAIL_URL.format(qid=qa_id), qa_id=qa_id,
            question=str(row.get("mainContent") or ""), answer=str(answer),
            questioner=str(row.get("authorName") or "investor"),
            question_date=_bj_date(row.get("pubDate")),
            answer_date=_bj_date(row.get("attachedPubDate")) or _bj_date(row.get("updateDate")),
            platform="互动易",
        ))
        if len(records) >= limit:
            break
    return records


# --------------------------------------------------------------------------- #
# 上证e互动 (Shanghai)
# --------------------------------------------------------------------------- #

def _sse_company_page(page: int) -> dict:
    payload = net.post_form_json(
        SSE_COMPANY_URL, {"code": "0", "order": "2", "areaId": "0", "page": str(page)},
        headers=SSE_HEADERS)
    return _parse_company_directory(payload.get("content") or "")


def _parse_company_directory(content: str) -> dict:
    """``{code: uid}`` from one directory page (single-quoted markup, unquoted uid)."""
    mapping = {}
    for block in content.split("companyBox")[1:]:
        code = _SSE_COMPANY_BLOCK.search(block)
        uid = _SSE_UID.search(block)
        if code and uid:
            mapping[code.group(1)] = uid.group(1)
    return mapping


def _sse_uid(code: str) -> str:
    """Binary-search the code-sorted company directory for one uid."""
    first = _sse_company_page(1)
    if code in first:
        return first[code]
    lo, hi, budget = 1, 74, _max_uid_pages()
    while lo <= hi and budget > 0:
        mid = (lo + hi) // 2
        budget -= 1
        mapping = _sse_company_page(mid)
        if not mapping:
            hi = mid - 1
            continue
        if code in mapping:
            return mapping[code]
        codes = sorted(mapping)
        if code < codes[0]:
            hi = mid - 1
        elif code > codes[-1]:
            lo = mid + 1
        else:
            break            # inside the page's range but absent
    raise net.FetchError(
        f"investor_qa_source_gap: 上证e互动 directory has no uid for {code} "
        "(delisted or not an SSE company)")


def _sse_records(thscode: str, code: str, *, as_of: str, market_scope: str,
                 limit: int) -> list[CanonicalRecord]:
    posture = POSTURES["sse_einteraction"]
    uid = _sse_uid(code)
    records: list[CanonicalRecord] = []
    seen: set[str] = set()
    for page in range(1, _max_feed_pages() + 1):
        query = urllib.parse.urlencode({
            "typeCode": "company", "type": "11", "pageSize": "100",
            "uid": uid, "page": str(page),
        })
        fragment = net.post_form_text(SSE_FEEDS_URL + "?" + query, {}, headers=SSE_HEADERS)
        items = _parse_sse_feed(fragment)
        if not items:
            break
        fresh = 0
        for item in items:
            qa_id = item["qa_id"]
            if not qa_id or qa_id in seen:
                continue        # paging can repeat an item; never emit it twice
            seen.add(qa_id)
            fresh += 1
            if not item["answer"]:
                continue
            records.append(_record(
                thscode=thscode, market_scope=market_scope, as_of=as_of, posture=posture,
                url=SSE_COMPANY_PAGE.format(uid=uid), qa_id=qa_id,
                question=item["question"], answer=item["answer"],
                questioner=item["questioner"], question_date=item["question_date"],
                answer_date=item["answer_date"], platform="上证e互动",
            ))
            if len(records) >= limit:
                return records
        if fresh == 0:
            break               # this page added nothing new: stop paging
    return records


class _SseFeedParser(HTMLParser):
    """Extract one record per ``div.m_feed_item`` (question, answer, dates, asker)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict] = []
        self._current: Optional[dict] = None
        self._text_class: Optional[str] = None
        self._text_depth = 0
        self._text_buf: list[str] = []
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        attrib = dict(attrs)
        classes = (attrib.get("class") or "").split()
        if tag == "div":
            self._depth += 1
            if (attrib.get("id") or "").startswith("item-"):
                self._current = {"qa_id": attrib["id"].replace("item-", ""),
                                 "texts": [], "dates": [], "questioner": ""}
                self.items.append(self._current)
            elif "m_feed_txt" in classes:
                self._text_class, self._text_buf, self._text_depth = "texts", [], self._depth
            elif "m_feed_from" in classes:
                self._text_class, self._text_buf, self._text_depth = "dates", [], self._depth
        elif tag == "a" and self._current is not None:
            # The asker anchor is marked rel="face" (not class), e.g.
            # <a rel="face" uid="287863" title="投资者_...">
            marks = set((attrib.get("rel") or "").split()) | set(classes)
            if "face" in marks and not self._current["questioner"]:
                self._current["questioner"] = attrib.get("title") or ""

    def handle_data(self, data):
        if self._text_class:
            self._text_buf.append(data)

    def handle_endtag(self, tag):
        if tag != "div":
            return
        # Only close the text block at the depth it opened, so nested markup inside a
        # question or answer does not truncate it.
        if self._text_class and self._depth == self._text_depth:
            text = _squash("".join(self._text_buf))
            if text and self._current is not None:
                self._current[self._text_class].append(text)
            self._text_class, self._text_buf = None, []
        self._depth -= 1


def _parse_sse_feed(fragment: str) -> list[dict]:
    parser = _SseFeedParser()
    parser.feed(fragment or "")
    items = []
    for raw in parser.items:
        texts, dates = raw["texts"], raw["dates"]
        if not texts:
            continue
        items.append({
            "qa_id": raw["qa_id"],
            "question": texts[0],
            "answer": texts[1] if len(texts) > 1 else "",
            "questioner": raw["questioner"],
            "question_date": _sse_date(dates[0] if dates else ""),
            "answer_date": _sse_date(dates[1] if len(dates) > 1 else
                                     (dates[0] if dates else "")),
        })
    return items


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _bj_date(epoch_ms) -> str:
    if epoch_ms in (None, ""):
        return ""
    try:
        return _dt.datetime.fromtimestamp(int(epoch_ms) / 1000, tz=CN_TZ).date().isoformat()
    except (TypeError, ValueError, OSError):
        return ""


_SSE_DATE = re.compile(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})")


def _sse_date(text: str) -> str:
    """e互动 prints human dates ('2026-09-30 15:04' / '2026年9月30日')."""
    match = _SSE_DATE.search(text or "")
    if not match:
        return ""
    year, month, day = (int(part) for part in match.groups())
    try:
        return _dt.date(year, month, day).isoformat()
    except ValueError:
        return ""
