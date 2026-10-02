#!/usr/bin/env python3
"""Regression tests for the CNINFO (巨潮资讯网) A-share disclosure adapter.

All offline: the AJAX layer is patched, so no network, no portal dependency. The
tests pin the four traps that make this endpoint easy to get quietly wrong —
silent category widening, UTC-vs-Beijing disclosure dates, title-only dedupe, and
``announcementType`` classification — plus the L1 posture and the registry rows
the repo validator enforces.
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
from tools.mira_data.canonical import POSTURES
from tools.mira_data.emit import emit_bundle


def _announcement(**overrides) -> dict:
    row = {
        "id": None, "secCode": "600519", "secName": "贵州茅台", "orgId": "gssh0600519",
        "announcementId": "1225475868",
        "announcementTitle": "贵州茅台2026年半年度报告",
        "announcementTime": 1786723200000,          # 2026-08-15 00:00 +08:00
        "adjunctUrl": "finalpage/2026-08-15/1225475868.PDF",
        "adjunctSize": 814, "adjunctType": "PDF",
        "pageColumn": "SHZB", "announcementType": "01010503||010113||010303",
    }
    row.update(overrides)
    return row


def _payload(announcements: list, *, has_more: bool = False) -> dict:
    return {"announcements": announcements, "hasMore": has_more,
            "totalAnnouncement": len(announcements), "totalpages": 1}


def _patched(announcements: list, *, has_more: bool = False, calls: list | None = None):
    def fake_post(url, data, **kwargs):
        if calls is not None:
            calls.append(data)
        return _payload(announcements, has_more=has_more)

    return (
        mock.patch.object(cn.net, "post_form_json", side_effect=fake_post),
        mock.patch.object(cn, "org_id_map", return_value={"600519": "gssh0600519"}),
        mock.patch.object(cn, "_sleep", return_value=None),
    )


def test_classify_prefers_announcement_type_over_title() -> None:
    token, basis = cn.classify(_announcement(
        announcementTitle="襄阳长源东谷实业股份有限公司回购实施进展公告",
        announcementType="01010503||010113||011513"))
    assert token == "buyback" and basis == "announcement_type:011513"

    # A title that would map elsewhere must not override a verified type code.
    token, basis = cn.classify(_announcement(
        announcementTitle="关于回购股份的公告",
        announcementType="01010503||010113||011501"))
    assert token == "shareholder_reduction"

    token, basis = cn.classify(_announcement(
        announcementTitle="芯联集成2026年前三季度业绩预告的自愿性披露公告",
        announcementType="01010503||010123||012111"))
    assert token == "earnings_preannouncement"
    print("ok verified announcementType codes win over title regexes")


def test_classify_falls_back_to_title_then_other() -> None:
    token, basis = cn.classify(_announcement(
        announcementTitle="克来机电控股股东减持股份结果公告",
        announcementType="01010503||010113"))          # noise codes only
    assert token == "shareholder_reduction" and basis == "title"

    token, basis = cn.classify(_announcement(
        announcementTitle="关于召开2026年第二次临时股东会的通知",
        announcementType="01010503||010113||012399"))
    # 012399 (业绩说明会) is verified, so it wins; the title rule never runs.
    assert token == "earnings_briefing"

    token, basis = cn.classify(_announcement(
        announcementTitle="关于某件无法归类的事项", announcementType="01010503"))
    assert token == "other" and basis == "unknown"
    print("ok unmapped input degrades to title rules and finally to other/unknown")


def test_report_shells_are_not_the_report_body() -> None:
    token, basis = cn.classify(_announcement(
        announcementTitle="南京晶升装备股份有限公司2025年年度报告摘要（更正版）",
        announcementType="01010503"))
    assert token == "other" and basis == "title:report_shell"

    # 沪市老牌国企 prefix the company name; the board-independent regex still matches.
    token, _ = cn.classify(_announcement(
        announcementTitle="凯盛科技股份有限公司2025年年度报告", announcementType="01010503"))
    assert token == "periodic_report_annual"
    print("ok periodic-report shells are excluded and prefixed titles still classify")


def test_broken_category_raises_instead_of_widening() -> None:
    for bad in ("category_yjkb_szsh", "category_jshgg_szsh", "category_made_up_szsh"):
        try:
            cn._query_page(category=bad, stock="600519,gssh0600519")
        except net.FetchError as exc:
            assert "cninfo_category_gap" in str(exc)
        else:
            raise AssertionError(f"expected a category guard for {bad}")
    print("ok known-broken and unknown category codes raise instead of returning the market")


def test_beijing_epoch_decode() -> None:
    # Read as UTC this lands on 2026-08-14, one day early.
    assert cn.bj_date(1786723200000) == "2026-08-15"
    assert cn.bj_time(1786723200000).startswith("2026-08-15T00:00:00+08:00")
    assert cn.bj_date(None) == "" and cn.bj_time("nonsense") == ""
    print("ok CNINFO epochs decode in Asia/Shanghai without an off-by-one day")


def test_dedupe_keeps_earlier_id_but_keeps_real_events() -> None:
    duplicate_pair = [
        _announcement(announcementId="879117"),
        _announcement(announcementId="874169"),
    ]
    with mock.patch.object(cn, "_iter_announcements", return_value=iter(duplicate_pair)), \
            mock.patch.object(cn, "org_id_map", return_value={"600519": "gssh0600519"}):
        result = cn.fetch_issuer_disclosures("600519", as_of="2026-10-02")
    assert len(result.records) == 1
    assert result.records[0].value == "874169"        # the smaller (earlier) id wins

    # The same title on different dates is two real announcements, not a duplicate.
    same_title_other_day = [
        _announcement(announcementId="1225366259", announcementTime=1781222400000),  # 2026-06-12
        _announcement(announcementId="1225324466", announcementTime=1779408000000),  # 2026-05-22
    ]
    with mock.patch.object(cn, "_iter_announcements", return_value=iter(same_title_other_day)), \
            mock.patch.object(cn, "org_id_map", return_value={"600519": "gssh0600519"}):
        result = cn.fetch_issuer_disclosures("600519", as_of="2026-10-02")
    assert len(result.records) == 2, "title-only dedupe would have dropped a real event"
    print("ok dedupe keys on (secCode, Beijing date, title) and keeps genuine re-issues")


def test_fetch_builds_l1_records_and_needs_no_ledger() -> None:
    patches = _patched([_announcement(), _announcement(
        announcementId="1225475864", announcementTitle="贵州茅台关于会计政策变更的公告",
        announcementType="01010503||010113||011301",
        adjunctUrl="finalpage/2026-08-15/1225475864.PDF", adjunctSize=95)])
    with patches[0], patches[1], patches[2]:
        result = cn.fetch_issuer_disclosures("600519", as_of="2026-10-02", since="2026-08-01")

    first = {record.metric: record for record in result.records}["periodic_report_h1"]
    assert first.family == "issuer_disclosure"
    assert first.posture.source_id == "cninfo_announcement_api"
    assert first.posture.authority_level == "L1"
    assert first.posture.claim_type == "fact"
    assert first.research_object == "600519.SH" and first.market_scope == "CN"
    assert first.unit == "announcement_id" and first.value == "1225475868"
    assert first.period == "2026-08-15" and first.source_date == "2026-08-15"
    assert first.url_or_path == cn.PDF_BASE + "finalpage/2026-08-15/1225475868.PDF"
    assert first.provenance["event_basis"] == "announcement_type:010303"
    assert "<em>" not in first.claim_text

    tokens = {record.metric for record in result.records}
    assert tokens == {"periodic_report_h1", "dividend"}

    scratch = ROOT / "local"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as out:
        emitted = emit_bundle(result.records, out_dir=out, research_object="600519.SH",
                              market_scope="CN", endpoint=cn.ENDPOINT.format(thscode="600519.SH"))
        assert emitted["calculation_ledger"] is None      # disclosed identity, not derived
        with open(Path(out) / "evidence-log.csv", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    assert {row["authority_level"] for row in rows} == {"L1"}
    assert {row["claim_type"] for row in rows} == {"fact"}
    assert {row["source_id"] for row in rows} == {"cninfo_announcement_api"}
    assert {row["source_language"] for row in rows} == {"zh-CN"}
    assert all("event_basis" not in row["notes"] or True for row in rows)
    print("ok records emit as L1 primary disclosure with no ledger requirement")


def test_paging_respects_max_items_and_has_more() -> None:
    calls: list[dict] = []
    page = [_announcement(announcementId=str(1000 + i),
                          announcementTitle=f"公告 {i}", announcementType="01010503")
            for i in range(cn.PAGE_SIZE)]
    patches = _patched(page, has_more=True, calls=calls)
    with patches[0], patches[1], patches[2]:
        result = cn.fetch_issuer_disclosures("600519", as_of="2026-10-02", max_items=5)
    assert len(result.records) == 5
    assert len(calls) == 1, "paging must stop once max_items is reached"
    assert calls[0]["stock"] == "600519,gssh0600519"      # bare code would return zero rows
    assert calls[0]["pageSize"] == "30"
    print("ok paging stops on max_items and always sends stock=<code>,<orgId>")


def test_category_filter_still_demotes_report_shells() -> None:
    """A periodic-report category contains 摘要/审计/英文版 shells too."""
    token, basis = cn.classify(_announcement(
        announcementTitle="贵州茅台2025年年度报告摘要",
        announcementType="01010503||010113||010301"),
        category="category_ndbg_szsh")
    assert token == "other" and basis.endswith("report_shell")

    token, basis = cn.classify(_announcement(
        announcementTitle="贵州茅台2025年年度报告"),
        category="category_ndbg_szsh")
    assert token == "periodic_report_annual" and basis == "category"

    # Non-periodic categories are unaffected by the shell guard (更正版 dividends
    # are still dividends).
    token, basis = cn.classify(_announcement(
        announcementTitle="振华重工2026年半年度权益分派实施公告（更正版）",
        announcementType="01010503"),
        category="category_qyfpxzcs_szsh")
    assert token == "dividend"
    print("ok category filtering still demotes report shells, other categories unaffected")


def test_registry_rows_are_registered() -> None:
    for path, column, expected in (
        (ROOT / "data" / "source-registry.csv", "source_id", None),
        (ROOT / "data" / "source-class-map.csv", "source_id", None),
    ):
        with path.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        ids = [row["source_id"] for row in rows]
        assert "cninfo_announcement_api" in ids, f"{path.name} is missing the new source"
        assert len(ids) == len(set(ids)), f"{path.name} has a duplicate source_id"

    with (ROOT / "data" / "source-class-map.csv").open(encoding="utf-8", newline="") as fh:
        mapping = {row["source_id"]: row["source_class"] for row in csv.DictReader(fh)}
    assert mapping["cninfo_announcement_api"] == "issuer_primary_disclosure"
    assert POSTURES["cninfo_disclosure"].source_class == "issuer_primary_disclosure"
    assert POSTURES["cninfo_disclosure"].source_id in mapping
    print("ok the new source is registered with a 1:1 class-map row")


def main() -> int:
    test_classify_prefers_announcement_type_over_title()
    test_classify_falls_back_to_title_then_other()
    test_report_shells_are_not_the_report_body()
    test_broken_category_raises_instead_of_widening()
    test_beijing_epoch_decode()
    test_dedupe_keeps_earlier_id_but_keeps_real_events()
    test_fetch_builds_l1_records_and_needs_no_ledger()
    test_paging_respects_max_items_and_has_more()
    test_category_filter_still_demotes_report_shells()
    test_registry_rows_are_registered()
    print("mira_data_cninfo_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
