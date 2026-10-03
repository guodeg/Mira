#!/usr/bin/env python3
"""Regression tests for the shareholder-count extraction (L1 report body).

Offline: HTTP and the PDF reader are patched. The field was chosen because it is the one
shareholder figure that survives the PDF text layer intact - the live filing prints the label
and the figure adjacent (``截至报告期末普通股股东总数(户) 296,404``). The tests pin that
narrowness: the three accepted phrasings, the period derived from the report title, the verbatim
sentence kept as the evidence trail, and an explicit refusal to claim the shareholder *tables*,
whose column binding the text layer loses.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import net
from tools.mira_data.adapters import cninfo_disclosure as cn


LIVE_SENTENCE = ("(二) 限售股份变动情况 □适用 √不适用 二、股东情况 (一) 股东总数： "
                 "截至报告期末普通股股东总数(户) 296,404 (二) 截至报告期末前十名股东")

REPORTS = [{"announcementTitle": "贵州茅台2026年半年度报告", "date_bj": "2026-08-15",
            "pdf_url": "http://static.cninfo.com.cn/finalpage/2026-08-15/x.PDF"},
           {"announcementTitle": "贵州茅台2025年年度报告", "date_bj": "2026-04-17",
            "pdf_url": "http://static.cninfo.com.cn/finalpage/2026-04-17/y.PDF"}]


def _fetch(text, *, reports=None, max_pages=40):
    rows = REPORTS if reports is None else reports
    with mock.patch.object(cn, "find_announcements", side_effect=lambda *a, **k: list(rows)), \
            mock.patch.object(cn, "extract_pdf_text",
                              return_value={"text": text, "page_count": 110, "pages_read": 40,
                                            "truncated": True, "chars": len(text), "url": "u"}):
        return cn.fetch_shareholder_count("600519", as_of="2026-10-03", max_pages=max_pages)


def test_live_sentence_yields_the_count_and_its_evidence() -> None:
    result = _fetch(LIVE_SENTENCE)
    record = result.records[0]
    assert record.family == "ownership_short_interest"
    assert record.metric == "shareholder_count" and record.value == 296404.0
    assert record.unit == "households"
    assert record.research_object == "600519.SH"
    assert record.period == "2026-06-30", "the half-year report title implies the period end"
    assert record.posture.source_id == "cninfo_announcement_api"
    assert record.posture.authority_level == "L1"
    assert record.posture.claim_type == "reported_metric"
    assert record.provenance["periodBasis"] == "derived from the report title"
    assert "296,404" in record.provenance["matchedSentence"], "the source sentence is kept"
    assert record.provenance["pagesRead"] == 40 and record.provenance["pageCount"] == 110
    assert "column binding" in record.provenance["tableCaveat"]
    print("ok the live phrasing yields the count with its period, sentence and caveat")


def test_accepted_phrasings_and_period_derivation() -> None:
    assert _fetch("股东总数（户）: 12,345 户").records[0].value == 12345.0
    assert _fetch("普通股股东总数： 8,001 户").records[0].value == 8001.0

    assert cn.report_period_end("贵州茅台2026年半年度报告") == "2026-06-30"
    assert cn.report_period_end("某某2025年年度报告") == "2025-12-31"
    assert cn.report_period_end("某某2026年第一季度报告") == "2026-03-31"
    assert cn.report_period_end("某某2026年半年度报告摘要") == "", "a summary is not the report"
    assert cn.report_period_end("某某关于回购的公告") == ""

    annual = _fetch("截至报告期末普通股股东总数(户) 1,000",
                    reports=[REPORTS[1]])
    assert annual.records[0].period == "2025-12-31"
    print("ok all three phrasings parse and the period comes from the report title")


def test_no_count_found_is_a_gap_never_a_zero() -> None:
    try:
        _fetch("本报告不披露股东情况。")
    except net.FetchError as exc:
        assert "cninfo_source_gap" in str(exc) and "not stated" in str(exc)
    else:
        raise AssertionError("a missing figure must be a gap, not a zero-valued claim")

    try:
        _fetch("截至报告期末普通股股东总数(户) 0")
    except net.FetchError as exc:
        assert "cninfo_source_gap" in str(exc)
    else:
        raise AssertionError("a zero must not be claimed as a shareholder count")
    print("ok an absent or zero total degrades to a labelled gap")


def test_pdf_layer_failures_are_reported_per_report() -> None:
    with mock.patch.object(cn, "find_announcements", side_effect=lambda *a, **k: list(REPORTS)), \
            mock.patch.object(cn, "extract_pdf_text",
                              side_effect=net.FetchError("cninfo_pdf_text_gap: image-only scan")):
        try:
            cn.fetch_shareholder_count("600519", as_of="2026-10-03")
        except net.FetchError as exc:
            assert "cninfo_source_gap" in str(exc) and "image-only scan" in str(exc)
        else:
            raise AssertionError("a scan-only report set must surface its reason")

    try:
        _fetch(LIVE_SENTENCE, reports=[])
    except net.FetchError as exc:
        assert "no periodic report found" in str(exc)
    else:
        raise AssertionError("no periodic report must be its own gap")
    print("ok unreadable bodies and missing reports both degrade with their reasons")


def main() -> int:
    test_live_sentence_yields_the_count_and_its_evidence()
    test_accepted_phrasings_and_period_derivation()
    test_no_count_found_is_a_gap_never_a_zero()
    test_pdf_layer_failures_are_reported_per_report()
    print("mira_data_shareholder_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
