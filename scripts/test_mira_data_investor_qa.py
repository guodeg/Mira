#!/usr/bin/env python3
"""Regression tests for the investor-Q&A adapters (互动易 / 上证e互动).

Offline: HTTP is patched and the e互动 response is a captured-shape HTML fragment.
The tests pin the decisions that matter — the two platforms cover different exchanges,
unanswered questions are not company speech, 互动易 epochs are UTC-ms to be read in
Beijing, e互动's uid is found by binary search rather than by pulling 73 pages, and the
claims are company-commentary grade rather than verified facts.
"""

from __future__ import annotations

import csv
import sys
import tempfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import net
from tools.mira_data.adapters import investor_qa as qa
from tools.mira_data.emit import emit_bundle


IRM_ORG = {"data": [{"secid": "gssz0000001", "shortName": "平安银行"}]}
IRM_QUESTIONS = {
    "totalPage": 1,
    "rows": [
        {   # answered -> becomes a claim
            "indexId": "1515236357817618432",
            "mainContent": "请问公司三季度经营情况如何？",
            "attachedContent": "公司三季度经营稳健，具体经营数据请以定期报告为准。" * 12,
            "attachedAuthor": "平安银行",
            "authorName": "irm1325043",
            "pubDate": 1786536000000,
            "attachedPubDate": 1786723200000,
        },
        {   # unanswered -> investor attention, not company speech
            "indexId": "1515236357817618433",
            "mainContent": "请问最新一期股东人数是多少？",
            "attachedContent": None,
            "authorName": "irm1325044",
            "pubDate": 1790834179000,
        },
    ],
}

SSE_FEED_HTML = """
<style> <!-- .feed_quote{margin-top: 25px;} --> </style>
<div class="m_feed_item" style="display: flex">
  <input type="hidden" id="currentPage" value="1" />
</div>
<div class="m_feed_item" id="item-1790373">
  <div class="m_feed_detail">
    <div class="m_feed_face">
      <a rel="face" uid="287863" href="user.do?uid=287863" title="投资者_1713067738000">
        <img title="投资者_1713067738000" src="avatar.png" />
      </a>
    </div>
    <div class="m_feed_txt">请问公司三季度经营情况如何？<div>补充：谢谢</div></div>
    <div class="m_feed_from">2026-09-30 15:04 来自：网站</div>
    <div class="m_feed_txt">公司生产经营正常，具体请见定期报告。</div>
    <div class="m_feed_from">2026-10-01 09:12 来自：网站</div>
  </div>
</div>
<div class="m_feed_item" id="item-1790374">
  <div class="m_feed_detail">
    <div class="m_feed_face"><a rel="face" uid="287864" title="投资者_2"></a></div>
    <div class="m_feed_txt">请问股东人数？</div>
    <div class="m_feed_from">2026-10-01 10:00 来自：网站</div>
  </div>
</div>
"""


def _sse_pages(per_page: int = 32, total_pages: int = 73):
    """Fake code-sorted company directory starting at 600000."""
    def page(number: int) -> dict:
        start = 600000 + (number - 1) * per_page
        blocks = []
        for offset in range(per_page):
            code = start + offset
            uid = 1000 + (number - 1) * per_page + offset
            blocks.append(
                f"<div class='companyBox'><a rel='tag' uid={uid} href='user.do?uid={uid}' "
                f"title='公司{code}'><img src='https://sns.sseinfo.com/resources/images/"
                f"avatar/company/{code}.png?random=1' /></a></div>")
        return {"content": "".join(blocks)}
    return page


def test_irm_skips_unanswered_and_labels_the_claim() -> None:
    calls = []

    def fake_post(url, data, **kwargs):
        calls.append(url)
        return IRM_ORG if "queryKeyboardInfo" in url else IRM_QUESTIONS

    with mock.patch.object(qa.net, "post_form_json", side_effect=fake_post):
        result = qa.fetch_investor_qa("000001", as_of="2026-10-02")
    assert len(result.records) == 1, "unanswered questions must not become company claims"

    record = result.records[0]
    assert record.family == "transcript_claim"
    assert record.metric == "investor_qa_answer"
    assert record.value == "1515236357817618432" and record.unit == "qa_id"
    assert record.posture.source_id == "irm_cninfo_api"
    assert record.posture.claim_type == "company_claim"
    assert record.posture.evidence_category == "company_statement"
    assert record.posture.authority_level == "L2"
    assert record.research_object == "000001.SZ"
    # The claim is an extract, not a copy: long text is truncated and the full text is
    # only reachable through the platform URL plus the provenance question.
    assert len(record.claim_text) < qa.CLAIM_TEXT_LIMIT + 80
    assert record.provenance["answerChars"] > qa.CLAIM_TEXT_LIMIT
    assert record.provenance["questionDate"] == "2026-08-12"
    assert record.provenance["answerDate"] == "2026-08-15"
    assert record.source_date == "2026-08-15"
    assert record.url_or_path.startswith("https://irm.cninfo.com.cn/ircs/question/questionDetail")
    assert len(calls) == 2
    print("ok 互动易 emits only answered pairs, as company-commentary claims")


def test_irm_epochs_are_read_in_beijing() -> None:
    assert qa._bj_date(1786723200000) == "2026-08-15"
    assert qa._bj_date(1786536000000) == "2026-08-12"
    assert qa._bj_date(None) == "" and qa._bj_date("junk") == ""
    print("ok 互动易 epoch milliseconds decode in Asia/Shanghai")


def test_sse_feed_parser_pairs_questions_and_answers() -> None:
    items = qa._parse_sse_feed(SSE_FEED_HTML)
    assert len(items) == 2, "the hidden-input block must not become an item"
    answered, unanswered = items
    assert answered["qa_id"] == "1790373"
    assert answered["question"].startswith("请问公司三季度经营情况如何？")
    assert "补充：谢谢" in answered["question"], "nested markup must not truncate the text"
    assert answered["answer"].startswith("公司生产经营正常")
    assert answered["questioner"] == "投资者_1713067738000"
    assert answered["question_date"] == "2026-09-30"
    assert answered["answer_date"] == "2026-10-01"
    assert unanswered["answer"] == ""
    print("ok the stdlib HTML parser pairs question, answer, dates and asker")


def test_sse_uid_uses_a_bounded_binary_search() -> None:
    page = _sse_pages()
    calls: list[int] = []

    def fake_page(number):
        calls.append(number)
        return qa._parse_company_directory(page(number)["content"])

    with mock.patch.object(qa, "_sse_company_page", side_effect=fake_page):
        uid = qa._sse_uid("600519")
    # 600519 is offset 519 from 600000: page 17 (16*32=512), slot 7 -> uid 1000+512+7.
    assert uid == "1519", uid
    assert len(calls) <= 10, f"uid lookup must stay bounded, took {len(calls)} pages"
    assert len(calls) < 73, "the whole directory must not be pulled"

    calls.clear()
    with mock.patch.object(qa, "_sse_company_page", side_effect=fake_page):
        try:
            qa._sse_uid("603999")
        except net.FetchError as exc:
            assert "investor_qa_source_gap" in str(exc)
        else:
            raise AssertionError("expected a gap for a code outside the directory")
    print("ok 上证e互动 uid lookup binary-searches the code-sorted directory")


def test_sse_records_only_answered_and_route_by_exchange() -> None:
    with mock.patch.object(qa, "_sse_uid", return_value="65"), \
            mock.patch.object(qa.net, "post_form_text", return_value=SSE_FEED_HTML):
        result = qa.fetch_investor_qa("600519", as_of="2026-10-02")
    assert len(result.records) == 1
    record = result.records[0]
    assert record.posture.source_id == "sse_einteraction_api"
    assert record.provenance["platform"] == "上证e互动"
    assert record.research_object == "600519.SH"

    # 互动易 has no Shanghai coverage and e互动 has no Shenzhen coverage; the Beijing
    # exchange has neither, and it must not spend a request finding that out.
    called = {"n": 0}

    def spy(*args, **kwargs):
        called["n"] += 1
        return {}

    with mock.patch.object(qa.net, "post_form_json", side_effect=spy), \
            mock.patch.object(qa.net, "post_form_text", side_effect=spy):
        try:
            qa.fetch_investor_qa("830799", as_of="2026-10-02")
        except net.FetchError as exc:
            assert "investor_qa_source_gap" in str(exc)
        else:
            raise AssertionError("expected a gap for a Beijing-exchange name")
    assert called["n"] == 0
    print("ok platform routing follows the exchange and BJ degrades for free")


def test_emitted_rows_are_company_statements() -> None:
    with mock.patch.object(qa, "_irm_org_id", return_value="gssz0000001"), \
            mock.patch.object(qa.net, "post_form_json", return_value=IRM_QUESTIONS):
        result = qa.fetch_investor_qa("000001", as_of="2026-10-02")
    scratch = ROOT / "local"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as out:
        emitted = emit_bundle(result.records, out_dir=out, research_object="000001.SZ",
                              market_scope="CN",
                              endpoint=qa.IRM_ENDPOINT.format(thscode="000001.SZ"))
        assert emitted["calculation_ledger"] is None
        with open(Path(out) / "evidence-log.csv", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    assert {row["claim_type"] for row in rows} == {"company_claim"}
    assert {row["evidence_category"] for row in rows} == {"company_statement"}
    assert {row["verification_status"] for row in rows} == {"unverified"}
    assert {row["source_speaker"] for row in rows} == {"company"}
    assert {row["authority_level"] for row in rows} == {"L2"}
    assert {row["source_language"] for row in rows} == {"zh-CN"}
    print("ok emitted rows are unverified L2 company statements, not facts")


def test_registry_rows_are_registered() -> None:
    with (ROOT / "data" / "source-registry.csv").open(encoding="utf-8", newline="") as fh:
        registry = [row["source_id"] for row in csv.DictReader(fh)]
    with (ROOT / "data" / "source-class-map.csv").open(encoding="utf-8", newline="") as fh:
        mapping = {row["source_id"]: row["source_class"] for row in csv.DictReader(fh)}
    for source_id in ("irm_cninfo_api", "sse_einteraction_api"):
        assert registry.count(source_id) == 1, f"{source_id} must appear once"
        assert mapping[source_id] == "regulatory_and_exchange"
    print("ok both investor-Q&A sources are registered with 1:1 class-map rows")


def main() -> int:
    test_irm_skips_unanswered_and_labels_the_claim()
    test_irm_epochs_are_read_in_beijing()
    test_sse_feed_parser_pairs_questions_and_answers()
    test_sse_uid_uses_a_bounded_binary_search()
    test_sse_records_only_answered_and_route_by_exchange()
    test_emitted_rows_are_company_statements()
    test_registry_rows_are_registered()
    print("mira_data_investor_qa_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
