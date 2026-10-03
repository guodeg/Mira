#!/usr/bin/env python3
"""Offline tests for the full-text-search fallback that gives the IR channel Shenzhen coverage.

The lesson these tests encode: the portal has **two indexes** and they are not
interchangeable. 投资者关系活动记录表 is absent from the announcement feed for Shenzhen names and
for the HK-line 海外监管公告 copies, but present in the full-text search index. The first
version of this channel queried the feed alone and concluded Shenzhen was unreachable — a
false gap produced by using the wrong index, not by missing data.

Equally pinned: the search index is a SUBSET, not the whole corpus (300611 and 300520 have
records that neither index returns), so the adapter must not claim completeness.
"""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import net
from tools.mira_data.adapters import cninfo_disclosure as cn


CN_TZ = _dt.timezone(_dt.timedelta(hours=8))


def _epoch_ms(day: str) -> int:
    """Beijing-midnight epoch, which is how the portal encodes announcement dates."""
    date = _dt.date.fromisoformat(day)
    return int(_dt.datetime(date.year, date.month, date.day, tzinfo=CN_TZ).timestamp() * 1000)


SAMPLE = """证券代码：301277 证券简称：新天地
投资者关系活动记录表
编号：2026-002
投资者关系活动
类别
☑特定对象调研 □业绩说明会
时间 2026 年 3 月 31 日
地点 公司会议室
投资者关系活动
主要内容介绍
1.公司主要产品的产能利用率如何？
答：公司主要产品产能利用率维持在较高水平。
2.未来是否有扩产计划？
答：将根据订单情况审慎推进。
"""


def _search_row(code="301277", title="新天地：投资者关系活动记录表",
                day="2026-03-31", ann_id="1225068128"):
    return {"secCode": code, "secName": "新天地", "announcementTitle": title,
            "announcementTime": _epoch_ms(day), "announcementId": ann_id,
            "adjunctUrl": f"finalpage/{day}/{ann_id}.PDF"}


def _post(batches):
    """Patch the search POST with a queue of page payloads."""
    return mock.patch.object(cn.net, "post_form_json", side_effect=list(batches))


def test_search_filters_titles_and_normalises_rows() -> None:
    page = {"totalAnnouncement": 3, "totalpages": 1, "announcements": [
        _search_row(title="新天地：投资者关系活动记录表"),
        _search_row(title="新天地：关于回购股份的公告", ann_id="9"),
        _search_row(code="09976", title="江波龙：海外监管公告 - 投资者关系活动记录表",
                    day="2026-09-29"),
    ]}
    with _post([page]):
        rows = cn.search_announcements_fulltext("投资者关系活动记录表",
                                                title_contains="投资者关系活动记录表")
    # The non-IR row is filtered out; the HK-code row is NOT (the caller scopes by code).
    assert len(rows) == 2, rows
    assert all("投资者关系活动记录表" in r["announcementTitle"] for r in rows)
    assert {r["secCode"] for r in rows} == {"301277", "09976"}
    row = rows[0]
    assert row["pdf_url"] == "http://static.cninfo.com.cn/finalpage/2026-03-31/1225068128.PDF"
    assert row["date_bj"] == "2026-03-31", row["date_bj"]
    print("ok search normalises rows to the feed's shape, filters titles, keeps HK codes visible")


def test_search_requests_the_capped_page_size_and_stops_short() -> None:
    full = {"totalAnnouncement": 200, "totalpages": 2,
            "announcements": [_search_row(ann_id=str(i)) for i in range(cn.FULLTEXT_PAGE_SIZE)]}
    short = {"totalAnnouncement": 200, "totalpages": 2, "announcements": [_search_row()]}
    with _post([full, short]) as poster:
        rows = cn.search_announcements_fulltext("x", max_pages=5)
    # pageSize is silently capped at 100 server-side, so the request must not overstate it.
    sent = poster.call_args_list[0][0][1]
    assert sent["pageSize"] == str(cn.FULLTEXT_PAGE_SIZE) == "100", sent
    assert sent["searchkey"] == "x" and sent["isfulltext"] == "false"
    # A short page ends the walk instead of trusting a page count.
    assert poster.call_count == 2, poster.call_count
    assert len(rows) == cn.FULLTEXT_PAGE_SIZE + 1
    print("ok the search asks for the capped page size and stops on a short page")


def test_search_respects_max_pages() -> None:
    full = {"totalAnnouncement": 999, "totalpages": 10,
            "announcements": [_search_row(ann_id=str(i)) for i in range(cn.FULLTEXT_PAGE_SIZE)]}
    with _post([full] * 10) as poster:
        cn.search_announcements_fulltext("x", max_pages=2)
    assert poster.call_count == 2, "max_pages must bound the walk"
    print("ok max_pages bounds the pagination")


def test_ir_activity_falls_back_to_search_and_scopes_by_code() -> None:
    search_pages = [
        {"totalAnnouncement": 2, "totalpages": 1, "announcements": [
            # The requested A-share record, plus the issuer's 5-digit HK-line copy of a
            # different activity: the HK row must not become a claim for this A-share symbol.
            _search_row(), _search_row(code="09976", ann_id="998"),
        ]},
    ]
    with mock.patch.object(cn, "find_announcements", return_value=[]), \
            _post(search_pages), \
            mock.patch.object(cn, "extract_pdf_text",
                              return_value={"text": SAMPLE, "page_count": 4, "pages_read": 4,
                                            "truncated": False, "chars": len(SAMPLE), "url": "u"}):
        result = cn.fetch_ir_activity("301277", since="2026-01-01", until="2026-10-03")
    assert result.records, "the search fallback must produce records"
    codes = {r.provenance["filingUrl"].split("/")[-1] for r in result.records}
    assert codes == {"1225068128.PDF"}, f"only the A-share record may be used: {codes}"
    assert all(r.provenance["sourceIndex"] == "fulltext_search" for r in result.records)
    assert all(r.research_object == "301277.SZ" for r in result.records)
    print("ok the fallback fires, and the HK-line copy is excluded by code")


def test_feed_takes_precedence_and_labels_its_index() -> None:
    filing = {"announcementTitle": "宁波精达2026年投资者关系活动记录表",
              "date_bj": "2026-09-30", "announcementId": "1225587176",
              "pdf_url": "http://static.cninfo.com.cn/finalpage/2026-09-30/1225587176.PDF"}
    with mock.patch.object(cn, "find_announcements", return_value=[filing]), \
            mock.patch.object(cn, "search_announcements_fulltext") as search, \
            mock.patch.object(cn, "extract_pdf_text",
                              return_value={"text": SAMPLE, "page_count": 4, "pages_read": 4,
                                            "truncated": False, "chars": len(SAMPLE), "url": "u"}):
        result = cn.fetch_ir_activity("603088", since="2026-07-01", until="2026-10-03")
    search.assert_not_called()
    assert all(r.provenance["sourceIndex"] == "announcement_feed" for r in result.records)
    print("ok the feed is the first index and the index used is recorded on every claim")


def test_both_indexes_empty_is_a_gap_naming_both() -> None:
    empty = {"totalAnnouncement": 0, "totalpages": 0, "announcements": []}
    with mock.patch.object(cn, "find_announcements", return_value=[]), _post([empty, empty]):
        try:
            cn.fetch_ir_activity("300611", since="2025-01-01", until="2026-10-03")
        except net.FetchError as exc:
            message = str(exc)
            assert "cninfo_source_gap" in message
            assert "300611.SZ" in message
            assert "announcement feed" in message and "full-text search" in message
        else:
            raise AssertionError("two empty indexes must be a labelled gap")
    print("ok an empty result from both indexes names both, instead of blaming one")


def test_period_is_iso_and_the_form_date_is_kept_verbatim() -> None:
    with mock.patch.object(cn, "find_announcements", return_value=[]), \
            _post([{"totalAnnouncement": 1, "totalpages": 1, "announcements": [_search_row()]}]), \
            mock.patch.object(cn, "extract_pdf_text",
                              return_value={"text": SAMPLE, "page_count": 4, "pages_read": 4,
                                            "truncated": False, "chars": len(SAMPLE), "url": "u"}):
        result = cn.fetch_ir_activity("301277", since="2026-01-01", until="2026-10-03")
    # The form prints 2026 年 3 月 31 日; `period` must stay ISO like every other adapter.
    assert {r.period for r in result.records} == {"2026-03-31"}, {r.period for r in result.records}
    assert all(r.provenance["activityDate"] for r in result.records)
    assert cn._ir_iso_date("2026 年 3 月 31 日") == "2026-03-31"
    assert cn._ir_iso_date("no date here") == ""
    print("ok period is ISO-8601 while the verbatim form date stays in provenance")


def main() -> int:
    test_search_filters_titles_and_normalises_rows()
    test_search_requests_the_capped_page_size_and_stops_short()
    test_search_respects_max_pages()
    test_ir_activity_falls_back_to_search_and_scopes_by_code()
    test_feed_takes_precedence_and_labels_its_index()
    test_both_indexes_empty_is_a_gap_naming_both()
    test_period_is_iso_and_the_form_date_is_kept_verbatim()
    print("mira_data_ir_search_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
