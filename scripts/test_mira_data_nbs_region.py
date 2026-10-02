#!/usr/bin/env python3
"""Regression tests for the NBS regional family (``macro_region``).

Offline: HTTP is patched. The behaviour pinned here was all learned by probing the live
endpoint, and each item is something that would otherwise fail quietly:

- ``showType=3`` is the only mode that returns the period x region matrix in one call, with
  regions labelled by ``area``; ``showType=2`` looks similar but collapses to the latest
  period, so reading the wrong one loses history without any error.
- The vendor intermittently answers ``success=true`` with an empty payload, which must be
  retried rather than reported as "no data".
- A region name that is not in the catalogue that was requested is dropped, never guessed,
  so a value cannot be filed under the wrong province.
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
from tools.mira_data.adapters import nbs_stats as nbs
from tools.mira_data.emit import emit_bundle


GDP = "aff57de5ee994283974705914fbed246"

REGION_CATALOG = {"data": [
    {"catalog_id": "a10dceae75d245008bf4b9a0e6fe1d55", "name_value": "110000000000",
     "show_name": "北京市", "name_text": "北京市"},
    {"catalog_id": "a10dceae75d245008bf4b9a0e6fe1d55", "name_value": "310000000000",
     "show_name": "上海市", "name_text": "上海市"},
    {"catalog_id": "a10dceae75d245008bf4b9a0e6fe1d55", "name_value": "440000000000",
     "show_name": "广东省", "name_text": "广东省"},
]}
INDICATOR_CATALOG = {"data": {"list": [
    {"_id": GDP, "i_showname": "地区生产总值 (亿元) ",
     "i_mark": "地区生产总值指按市场价格计算的一个地区所有常住单位在一定时期内生产活动的最终成果"},
]}}

# showType=3: outer nodes are PERIODS; inside each, one entry per region under ``area``.
MATRIX = {"success": True, "message": "成功", "data": [
    {"code": "2026YY", "name": "2026年", "values": [
        {"area": "北京市", "value": ""}, {"area": "上海市", "value": ""},
        {"area": "广东省", "value": ""}]},
    {"code": "2025YY", "name": "2025年", "values": [
        {"area": "北京市", "value": "52073.4"}, {"area": "上海市", "value": "56708.7"},
        {"area": "广东省", "value": "145846.8"}]},
    {"code": "2024YY", "name": "2024年", "values": [
        {"area": "北京市", "value": "49670.2"}, {"area": "上海市", "value": "53759.5"},
        {"area": "广东省", "value": "141488.9"}]},
]}
EMPTY = {"success": True, "message": "成功", "data": []}


def _session_stub(responses, captured, catalog=INDICATOR_CATALOG):
    calls = {"n": 0}

    def fake_post_json(url, payload, **kwargs):
        assert "/stream/esData" in url, url
        captured.update(payload)
        index = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return responses[index]

    def fake_get_json(url, **kwargs):
        if nbs.DAS_PATH in url:
            return REGION_CATALOG
        return catalog

    class Stub:
        post_json = staticmethod(fake_post_json)
        get_json = staticmethod(fake_get_json)
    return Stub(), calls


def _run(series="REGION_GDP", regions=None, *, responses=(MATRIX,), kind="province"):
    (ROOT / "local").mkdir(exist_ok=True)
    scratch = tempfile.mkdtemp(dir=ROOT / "local")
    captured: dict = {}
    stub, calls = _session_stub(list(responses), captured)
    with mock.patch.object(nbs, "_cache_dir", return_value=Path(scratch)), \
            mock.patch.object(nbs, "_session", return_value=stub), \
            mock.patch.object(nbs, "_pause", return_value=0.0):
        result = nbs.fetch_nbs_region(series, regions, as_of="2026-10-02", kind=kind)
    return result, captured, calls


def test_multi_region_matrix_uses_showtype_3_and_keeps_the_history() -> None:
    result, payload, _calls = _run(regions=["北京", "上海", "广东"])
    assert payload["showType"] == "3", "the period x region matrix is the only usable mode"
    assert [row["value"] for row in payload["das"]] == ["110000000000", "310000000000",
                                                       "440000000000"]
    assert {r.research_object for r in result.records} == {"CN_110000", "CN_310000",
                                                          "CN_440000"}
    record = next(r for r in result.records if r.research_object == "CN_110000")
    assert record.family == "macro_series" and record.metric == "region_gdp"
    assert record.value == 52073.4 and record.unit == "CNY_100m" and record.currency == "CNY"
    assert record.period == "2025年", "the empty 2026 placeholder must not win"
    assert record.posture.source_id == "nbs_stats_api"
    assert record.posture.authority_level == "L2" and record.posture.claim_type == "fact"
    assert record.provenance["regionName"] == "北京市"
    assert record.provenance["regionCode"] == "110000000000"
    assert record.provenance["periodsInPayload"] == 2
    assert record.provenance["regionsRequested"] == 3
    assert "area" in record.provenance["regionModeNote"]
    assert "地区生产总值指按市场价格计算" in record.provenance["statisticalBasis"]

    series = result.series
    assert series["name"] == "nbs-region-REGION_GDP"
    assert series["columns"] == ["period", "北京市", "上海市", "广东省"]
    assert [row["period"] for row in series["rows"]] == ["2024年", "2025年"]
    assert series["rows"][-1]["广东省"] == 145846.8
    print("ok showType=3 returns the whole matrix: records per region plus a wide panel")


def test_single_region_read_still_uses_the_matrix_mode() -> None:
    result, payload, _calls = _run(regions=["北京"])
    assert payload["showType"] == "3"
    assert len(payload["das"]) == 1
    assert len(result.records) == 1
    assert result.records[0].research_object == "CN_110000"
    assert result.series["columns"] == ["period", "北京市"]
    assert len(result.series["rows"]) == 2
    print("ok a single region uses the same mode and yields a one-column panel")


def test_empty_payload_is_retried_instead_of_reported_as_no_data() -> None:
    result, _payload, calls = _run(regions=["北京"], responses=(EMPTY, MATRIX))
    assert len(result.records) == 1, "the retry must recover the data"
    assert calls["n"] == 2, f"expected one retry, saw {calls['n']} posts"
    print("ok an empty success payload is retried instead of reported as no data")


def test_empty_after_retries_is_a_gap_and_unknown_regions_are_never_guessed() -> None:
    try:
        _run(regions=["北京"], responses=(EMPTY, EMPTY, EMPTY))
    except net.FetchError as exc:
        assert "no published observation" in str(exc) and "attempt" in str(exc)
    else:
        raise AssertionError("a persistent empty payload must surface as a source gap")

    foreign = {"success": True, "data": [
        {"code": "2025YY", "name": "2025年", "values": [{"area": "香港特别行政区",
                                                        "value": "1"}]}]}
    try:
        _run(regions=["北京"], responses=(foreign,))
    except net.FetchError as exc:
        assert "not in the catalogue" in str(exc)
    else:
        raise AssertionError("a region outside the requested catalogue must not be filed")

    mixed = {"success": True, "data": [
        {"code": "2025YY", "name": "2025年", "values": [
            {"area": "北京市", "value": "52073.4"},
            {"area": "香港特别行政区", "value": "999"}]}]}
    result, _payload, _calls = _run(regions=["北京"], responses=(mixed,))
    assert len(result.records) == 1 and result.records[0].value == 52073.4
    print("ok empty-after-retries is a gap, and a stray region is dropped rather than guessed")


def test_region_resolution_and_input_validation() -> None:
    scratch = Path(tempfile.mkdtemp(dir=str(ROOT / "local")))
    with mock.patch.object(nbs, "_cache_dir", return_value=scratch), \
            mock.patch.object(nbs, "_session") as session:
        session.return_value.get_json.return_value = REGION_CATALOG
        assert [r["name"] for r in nbs.resolve_regions(["北京", "广东"])] == ["北京市", "广东省"]
        assert [r["name"] for r in nbs.resolve_regions(["北京市"])] == ["北京市"]
        assert len(nbs.resolve_regions(None)) == 3
        try:
            nbs.resolve_regions(["火星"])
        except net.FetchError as exc:
            assert "unknown region" in str(exc) and "北京市" in str(exc)
        else:
            raise AssertionError("an unknown region must error, not guess a neighbour")

    for call, token in ((lambda: nbs.fetch_nbs_region("NOPE"), "invalid_series"),
                        (lambda: nbs.list_regions("planet"), "invalid_region_kind"),
                        (lambda: nbs._root_for("weekly"), "invalid_frequency")):
        try:
            call()
        except net.FetchError as exc:
            assert token in str(exc), (token, str(exc))
        else:
            raise AssertionError(f"expected {token}")
    assert nbs._dts_window("annual", "2026-10-02") == "2016YY-2026YY"
    assert nbs._region_period_label("2025YY", None) == "2025年"
    assert nbs._region_period_label("2025YY", "2025年") == "2025年"
    assert nbs._region_period_label("202604SS", None) == "2026Q4"
    print("ok tolerant resolution, loud failures, and period labels in every encoding")


def test_emitted_rows_are_official_l2_and_the_registry_mentions_regions() -> None:
    result, _payload, _calls = _run(regions=["北京", "上海"])
    scratch = ROOT / "local"
    with tempfile.TemporaryDirectory(dir=scratch) as out:
        emitted = emit_bundle(result.records, out_dir=out, research_object="CN_110000",
                              market_scope="CN",
                              endpoint=nbs.ENDPOINT.format(symbol="REGION_GDP"))
        assert emitted["calculation_ledger"] is None
        with open(Path(out) / "evidence-log.csv", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    assert {row["claim_type"] for row in rows} == {"fact"}
    assert {row["authority_level"] for row in rows} == {"L2"}
    assert {row["verification_status"] for row in rows} == {"verified"}
    assert {row["source_id"] for row in rows} == {"nbs_stats_api"}
    assert all("regionCode=" in row["notes"] for row in rows)

    with (ROOT / "data" / "source-registry.csv").open(encoding="utf-8", newline="") as fh:
        registry = list(csv.DictReader(fh))
    row = next(entry for entry in registry if entry["source_id"] == "nbs_stats_api")
    assert row["authority_level"] == "L2" and "provincial" in row["notes"]
    print("ok regional rows emit as verified L2 official statistics with their region code")


def main() -> int:
    test_multi_region_matrix_uses_showtype_3_and_keeps_the_history()
    test_single_region_read_still_uses_the_matrix_mode()
    test_empty_payload_is_retried_instead_of_reported_as_no_data()
    test_empty_after_retries_is_a_gap_and_unknown_regions_are_never_guessed()
    test_region_resolution_and_input_validation()
    test_emitted_rows_are_official_l2_and_the_registry_mentions_regions()
    print("mira_data_nbs_region_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
