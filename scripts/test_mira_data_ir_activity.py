#!/usr/bin/env python3
"""Regression tests for the filed IR-activity record channel (投资者关系活动记录表).

Offline: HTTP and the PDF reader are patched; the text sample is the real layout captured from a
live filing (宁波精达 2026 年半年度业绩说明会). What is pinned: the form's labels carry **no
colons**, the activity category is a **checkbox list**, questions are **numbered** and answers
start with ``答：``. Those three shapes are why the parser is written the way it is, and why only
extracts (never full copies) reach the evidence log.
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
from tools.mira_data.adapters import cninfo_disclosure as cn
from tools.mira_data.emit import emit_bundle


SAMPLE = """证券代码：603088 证券简称：宁波精达
宁波精达成形装备股份有限公司
投资者关系活动记录表（2026 年半年度业绩说明会）
编号：2026-005
投资者关系活动
类别
□特定对象调研 分析师会议
□媒体采访 ☑业绩说明会
□新闻发布会 □路演活动
现场参观
□其他
参与单位名称及
人员姓名
线上参与公司 2026年半年度业绩说明会的投资者
时间 2026 年 9 月 29 日
地点 上 海 证 券 报 · 中 国 证 券 网 路 演 中 心
上市公司接待人
员姓名
总经理：李永坚先生
投资者关系活动
主要内容介绍
网络互动
1.海外的人员配置构成情况请简要说明谢谢！
答：公司产品销售全球 80个国家地区，公司已建立了经验丰富的服务团队。
2.公司上半年财务费用同比增幅较大，主要原因是什么？
答：上半年财务费用变动的主要原因是外币汇兑收益减少。
"""

FILING = {"announcementTitle": "宁波精达2026年投资者关系活动记录表(2026年半年度业绩说明会）",
          "date_bj": "2026-09-30", "announcementId": "1225587176",
          "pdf_url": "http://static.cninfo.com.cn/finalpage/2026-09-30/1225587176.PDF"}


def _fetch(text=SAMPLE, filings=None, **kwargs):
    rows = [FILING] if filings is None else filings
    with mock.patch.object(cn, "find_announcements", side_effect=lambda *a, **k: list(rows)), \
            mock.patch.object(cn, "extract_pdf_text",
                              return_value={"text": text, "page_count": 4, "pages_read": 4,
                                            "truncated": False, "chars": len(text), "url": "u"}):
        return cn.fetch_ir_activity("603088", since="2026-07-01", until="2026-10-03", **kwargs)


def test_form_header_parses_without_colons_and_with_checkboxes() -> None:
    parsed = cn.parse_ir_activity(SAMPLE)
    assert parsed["number"] == "2026-005"
    assert parsed["date"] == "2026 年 9 月 29 日"
    assert parsed["venue"].startswith("上 海 证 券 报")
    assert parsed["participants"] == "线上参与公司 2026年半年度业绩说明会的投资者"
    assert parsed["receptionists"].startswith("总经理：李永坚先生")
    assert parsed["category"] == "业绩说明会", "the checked box is the category"
    assert parsed["categories_all"] == ["业绩说明会"]

    both = SAMPLE.replace("□特定对象调研 分析师会议", "☑特定对象调研 分析师会议")
    assert cn.parse_ir_activity(both)["categories_all"] == ["特定对象调研", "业绩说明会"]
    print("ok the header parses from colon-less labels and checkbox categories")


def test_numbered_questions_pair_with_the_answer_marker() -> None:
    pairs = cn.parse_ir_activity(SAMPLE)["qa_pairs"]
    assert len(pairs) == 2, pairs
    question, answer = pairs[0]
    assert question.startswith("海外的人员配置构成情况"), question
    assert answer.startswith("公司产品销售全球"), answer
    assert pairs[1][0].startswith("公司上半年财务费用"), pairs[1][0]
    assert pairs[1][1].startswith("上半年财务费用变动"), pairs[1][1]
    print("ok numbered questions pair with 答： answers")


def test_records_are_company_claims_with_the_header_in_provenance() -> None:
    result = _fetch()
    assert len(result.records) == 2
    record = result.records[0]
    assert record.family == "transcript_claim" and record.metric == "ir_qa_answer"
    assert record.research_object == "603088.SH"
    assert record.value == "1225587176-1" and record.unit == "qa_id"
    assert record.period == "2026 年 9 月 29 日"
    assert record.posture.source_id == "cninfo_announcement_api"
    assert record.posture.authority_level == "L1"
    assert record.posture.claim_type == "company_claim"
    assert record.posture.evidence_category == "company_statement"
    provenance = record.provenance
    assert provenance["activityCategory"] == "业绩说明会"
    assert provenance["participants"].startswith("线上参与公司")
    assert provenance["qaCount"] == 2 and provenance["qaIndex"] == 1
    assert provenance["question"].startswith("海外的人员配置构成情况")
    assert "tone" in provenance["notExtracted"], "the non-extraction must be stated"
    assert len(record.claim_text) < cn.IR_ANSWER_LIMIT + 80, "claims are extracts, not copies"
    print("ok records are L1 company claims carrying the whole activity header")


def test_caps_and_gaps() -> None:
    assert len(_fetch(max_items=1).records) == 1, "the item cap bounds the emitted pairs"

    try:
        _fetch(text="本公司不适用。")
    except net.FetchError as exc:
        assert "no numbered Q&A" in str(exc)
    else:
        raise AssertionError("a filing without Q&A must be a labelled gap")

    try:
        _fetch(filings=[])
    except net.FetchError as exc:
        assert "cninfo_source_gap" in str(exc) and "互动易" in str(exc)
    else:
        raise AssertionError("the Shenzhen disclosure route must be named in the gap")

    try:
        _fetch(filings=[dict(FILING, announcementTitle="某某公司关于分红的公告")])
    except net.FetchError as exc:
        assert "cninfo_source_gap" in str(exc)
    else:
        raise AssertionError("non-IR filings must not be parsed as activity records")
    print("ok caps hold and every empty case names its reason")


def test_emitted_rows_are_company_statements() -> None:
    result = _fetch()
    scratch = ROOT / "local"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as out:
        emitted = emit_bundle(result.records, out_dir=out, research_object="603088.SH",
                              market_scope="CN", endpoint="https://static.cninfo.com.cn/x.PDF")
        assert emitted["calculation_ledger"] is None
        with open(Path(out) / "evidence-log.csv", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    assert {row["claim_type"] for row in rows} == {"company_claim"}
    assert {row["evidence_category"] for row in rows} == {"company_statement"}
    assert {row["authority_level"] for row in rows} == {"L1"}
    assert {row["verification_status"] for row in rows} == {"unverified"}
    assert all("activityCategory=业绩说明会" in row["notes"] for row in rows)
    print("ok emitted rows are unverified L1 company statements with the activity header")


def main() -> int:
    test_form_header_parses_without_colons_and_with_checkboxes()
    test_numbered_questions_pair_with_the_answer_marker()
    test_records_are_company_claims_with_the_header_in_provenance()
    test_caps_and_gaps()
    test_emitted_rows_are_company_statements()
    print("mira_data_ir_activity_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
