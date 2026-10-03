#!/usr/bin/env python3
"""Offline tests for the 十大股东 / 十大流通股东 channel (L5 relay).

Three things these tests exist to prevent:

1. **Reading a ratio off the wrong denominator.** The two tables publish shares of 总股本 and
   shares of 流通A股. A merged or mislabelled record would silently compare them.
2. **Letting a computed figure in.** A sum or concentration ratio would be a Mira calculation
   and need a ledger row, so no such metric may appear here.
3. **Mixing two periods in one page.** The relay cannot be asked for "the latest period", so
   the newest END_DATE is read first and then pinned by equality; without the pin a page holds
   two periods and ranks repeat.
"""

from __future__ import annotations

import sys
import urllib.parse
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import net
from tools.mira_data.adapters import em_holders as eh


def _row(rank, name, shares, ratio, change, total=1250081601, date="2026-06-30 00:00:00"):
    return {"END_DATE": date, "HOLDER_RANK": rank, "HOLDER_NAME": name, "HOLD_NUM": shares,
            "HOLD_NUM_RATIO": ratio, "HOLD_NUM_CHANGE": change, "TOTAL_SHARES_NUM": total,
            "SHARES_TYPE": "流通A股", "HOLDER_STATE": "", "SECURITY_CODE": "600519"}


TOP10 = {"result": {"count": 872, "data": [
    _row(1, "中国贵州茅台酒厂(集团)有限责任公司", 681282935, 54.5, None),
    _row(2, "贵州省国有资本运营有限责任公司", 56996777, 4.56, 0),
    _row(3, "香港中央结算有限公司", 53711656, 4.3, -5021413),
]}}

FREE_FLOAT = {"result": {"count": 940, "data": [
    {**_row(1, "中国贵州茅台酒厂(集团)有限责任公司", 681282935, None, None),
     "FREE_HOLDNUM_RATIO": 54.499077056651},
    {**_row(2, "香港中央结算有限公司", 53711656, None, -5021413),
     "FREE_HOLDNUM_RATIO": 4.296651991121},
]}}

# The two-step read per table: probe the newest period (pageSize=1), then pin it by equality.
# Order matters - the adapter finishes both calls for one table before starting the next.
PROBE_TOP10 = {"result": {"count": 872, "data": [_row(1, "probe", 1, 1.0, None)]}}
PROBE_FREE_FLOAT = {"result": {"count": 940, "data": [_row(1, "probe", 1, 1.0, None)]}}
LATEST = [PROBE_TOP10, TOP10, PROBE_FREE_FLOAT, FREE_FLOAT]


def _fetch(payloads=None, **kwargs):
    seq = LATEST if payloads is None else payloads
    with mock.patch.object(eh.net, "get_json", side_effect=list(seq)) as getter:
        result = eh.fetch_shareholders("600519", as_of="2026-10-03", **kwargs)
    return result, getter


def _by_metric(records):
    out: dict[str, list] = {}
    for rec in records:
        out.setdefault(rec.metric, []).append(rec)
    return out


def test_units_and_ratio_basis_are_verified() -> None:
    result, getter = _fetch()
    grouped = _by_metric(result.records)

    shares = grouped["holder_shares"][0]
    assert shares.value == 681282935.0 and shares.unit == "shares"
    assert shares.family == "ownership_short_interest"
    assert shares.period == "2026-06-30" and shares.period_type == "point_in_time"
    assert shares.research_object == "600519.SH"
    assert shares.posture.authority_level == "L5"
    assert shares.posture.claim_type == "reported_metric"
    assert "HOLD_NUM" not in shares.metric, "a raw vendor tag must not become the metric name"

    # The published ratio is carried, and it reconciles with the published share count:
    # 681282935 / 1250081601 = 54.4999...%, and the vendor publishes 54.5.
    ratio = grouped["holder_holding_ratio"][0]
    assert ratio.value == 54.5 and ratio.unit == "percent"
    total = shares.provenance["totalSharesNum"]
    assert abs(681282935 / total * 100 - ratio.value) < 0.01, "share basis must be 总股本"

    # The free-float row is a different denominator and says so rather than reusing the label.
    float_ratio = grouped["holder_free_float_ratio"][0]
    assert float_ratio.value == 54.499077056651
    assert "流通股" in float_ratio.provenance["ratioBasis"]
    assert float_ratio.provenance["ratioField"] == "FREE_HOLDNUM_RATIO"
    assert "总股本" not in float_ratio.provenance["ratioBasis"]
    assert "流通股" in float_ratio.claim_text and "总股本" not in float_ratio.claim_text
    print("ok units reconcile on 总股本, and the free-float ratio carries its own denominator")


def test_no_derived_concentration_is_claimed() -> None:
    result, _getter = _fetch()
    metrics = {rec.metric for rec in result.records}
    for forbidden in ("top10_sum", "concentration", "top10_total", "free_float_pct",
                      "holder_sum", "top10_concentration"):
        assert forbidden not in metrics, f"{forbidden} would be a Mira calculation"
    assert not any(rec.derived for rec in result.records), "no row here is a Mira calculation"
    assert "no sum" in result.records[0].provenance["notDerived"]
    assert "ledger" in result.records[0].provenance["notDerived"]
    print("ok no sum or concentration ratio is claimed, so no ledger row is owed")


def test_the_two_tables_stay_separate() -> None:
    result, _getter = _fetch()
    grouped = _by_metric(result.records)
    # Distinct rank metrics: rank by total holding and rank among tradable holders differ.
    assert "holder_top10_rank" in grouped and "holder_free_float_rank" in grouped
    assert "holder_rank" not in grouped, "an unqualified rank would be ambiguous between tables"
    assert grouped["holder_top10_rank"][0].provenance["tableKey"] == "top10"
    assert grouped["holder_free_float_rank"][0].provenance["tableKey"] == "free_float"
    assert "前十名股东" in grouped["holder_top10_rank"][0].provenance["table"]
    assert "前十名流通股东" in grouped["holder_free_float_rank"][0].provenance["table"]
    # The upgrade route to L1 is on every record, because the publisher is an aggregator.
    assert all("issuer" in rec.provenance["upgradePath"] for rec in result.records)
    print("ok both tables are read separately, labelled, and name their L1 upgrade route")


def test_latest_period_is_pinned_by_equality() -> None:
    result, getter = _fetch()
    urls = [urllib.parse.unquote(call[0][0]) for call in getter.call_args_list]
    assert len(urls) == 4, "one probe per table plus one pinned read per table"
    assert "sortColumns=END_DATE" in urls[0], "the probe asks for the newest period first"
    assert "pageSize=1" in urls[0], "the probe needs only the newest period, not a page of rows"
    for pinned in (urls[1], urls[3]):
        assert '(END_DATE=\'2026-06-30\')' in pinned, pinned
    assert "RPT_F10_EH_HOLDERS" in urls[1] and "RPT_F10_EH_FREEHOLDERS" in urls[3]
    assert "HOLDER_RANK" in urls[1], "ranks come back ordered, not in vendor row order"
    assert {rec.period for rec in result.records} == {"2026-06-30"}, "one period only"

    # A row from a neighbouring period inside the same response must not become a claim.
    bleed = {"result": {"count": 9, "data": [
        _row(1, "本期", 100, 1.0, None),
        _row(1, "上期", 90, 0.9, None, date="2026-03-31 00:00:00"),
    ]}}
    payloads = [{"result": {"count": 9, "data": [_row(1, "probe", 1, 1.0, None)]}}, bleed]
    result, _g = _fetch(payloads, table="top10")
    assert all(rec.period == "2026-06-30" for rec in result.records)
    assert not any("上期" in rec.claim_text for rec in result.records), "no bleed from 2026-03-31"
    print("ok the newest period is pinned by equality and a neighbouring period cannot bleed in")


def test_missing_fields_are_absent_not_zero() -> None:
    sparse = {"result": {"count": 3, "data": [
        {"END_DATE": "2026-06-30 00:00:00", "HOLDER_RANK": 1, "HOLDER_NAME": "无变动股东",
         "HOLD_NUM": 100, "HOLD_NUM_RATIO": 0.5, "HOLD_NUM_CHANGE": "不变",
         "TOTAL_SHARES_NUM": 20000, "SECURITY_CODE": "600519"},
        {"END_DATE": "2026-06-30 00:00:00", "HOLDER_RANK": 2, "HOLDER_NAME": None,
         "HOLD_NUM": None, "HOLD_NUM_RATIO": None, "HOLD_NUM_CHANGE": "新进",
         "SECURITY_CODE": "600519"},
    ]}}
    payloads = [{"result": {"count": 3, "data": [{"END_DATE": "2026-06-30 00:00:00",
                                                  "HOLDER_RANK": 1}]}}, sparse]
    result, _g = _fetch(payloads, table="top10")
    grouped = _by_metric(result.records)
    # "不变"/"新进" are states, not numbers: they must not become a 0.0 change claim.
    assert "holder_share_change" not in grouped, "a labelled state is not a zero change"
    assert grouped["holder_shares"][0].provenance["holderState"] == ""
    # The nameless row still yields its rank, with the absence labelled rather than invented.
    ranks = grouped["holder_top10_rank"]
    assert ranks[1].claim_text.endswith("未标注股东名称")
    assert len(grouped["holder_shares"]) == 1, "a null share count yields no share claim"

    empty = [{"result": {"count": 0, "data": []}}]
    with mock.patch.object(eh.net, "get_json", side_effect=empty):
        try:
            eh.fetch_shareholders("600519", as_of="2026-10-03", table="top10")
        except net.FetchError as exc:
            assert "eastmoney_source_gap" in str(exc) and "600519" in str(exc)
        else:
            raise AssertionError("an empty vendor answer must be a labelled gap")

    # History exists but the probe row carries no date at all: still a gap, never a silent skip.
    undated = [{"result": {"count": 40, "data": [{"HOLDER_NAME": "no date"}]}}]
    with mock.patch.object(eh.net, "get_json", side_effect=undated):
        try:
            eh.fetch_shareholders("600519", as_of="2026-10-03", table="top10")
        except net.FetchError as exc:
            assert "usable" in str(exc) and "END_DATE" in str(exc)
        else:
            raise AssertionError("a row without a period cannot anchor a disclosure claim")

    # History exists but nothing inside the newest period: report it, do not fall back.
    stale_only = [{"result": {"count": 40, "data": [_row(1, "probe", 1, 1.0, None)]}},
                  {"result": {"count": 40, "data": [_row(1, "上期", 1, 1.0, None,
                                                        date="2026-03-31 00:00:00")]}}]
    with mock.patch.object(eh.net, "get_json", side_effect=stale_only):
        try:
            eh.fetch_shareholders("600519", as_of="2026-10-03", table="top10")
        except net.FetchError as exc:
            assert "latest period" in str(exc)
        else:
            raise AssertionError("rows from an older period cannot substitute for the latest")
    print("ok absent fields stay absent, states are not numbers, and empties are gaps")


def test_table_selector_and_cap() -> None:
    for bad in ("top20", "holders"):
        try:
            eh._tables(bad)
        except net.FetchError as exc:
            assert "unknown shareholder table" in str(exc)
        else:
            raise AssertionError("an unknown table selector must be rejected, not defaulted")

    result, _g = _fetch([{"result": {"count": 9, "data": [_row(1, "probe", 1, 1.0, None)]}},
                         {"result": {"count": 9, "data": [_row(i, f"h{i}", i, 1.0, None)
                                                         for i in range(1, 11)]}}],
                        table="top10", max_holders=3)
    assert len(_by_metric(result.records)["holder_top10_rank"]) == 3
    assert eh._max_holders(None) == 10 and eh._max_holders(99) == 10 and eh._max_holders(0) == 1
    print("ok the table selector is explicit and the holder cap is bounded")


def main() -> int:
    test_units_and_ratio_basis_are_verified()
    test_no_derived_concentration_is_claimed()
    test_the_two_tables_stay_separate()
    test_latest_period_is_pinned_by_equality()
    test_missing_fields_are_absent_not_zero()
    test_table_selector_and_cap()
    print("mira_data_holders_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
