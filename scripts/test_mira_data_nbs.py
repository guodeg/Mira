#!/usr/bin/env python3
"""Regression tests for the official NBS data-library adapter.

Offline: HTTP is patched. These tests pin the behaviour that made an earlier probe of this
same agency look unreachable — the endpoint needs a POST envelope, unpublished periods come
back as the literal string 无 rather than as absent keys, and the statistical basis (口径)
lives in a different endpoint than the data. They also pin the tier: this is the official
source, so it is L2 and it records the exact catalogue/indicator pair it read.
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


CPI_YOY = "53180dfb9c14411ba4b762307c85920c"
CPI_MOM = "f3904a1f5a384d54a3944ec6e2df3d1c"

CATALOG = {"data": {"list": [
    {"_id": CPI_YOY, "i_showname": "居民消费价格指数 (上年同月=100) ",
     "i_mark": "居民消费价格指数反映一定时期内城乡居民所购买的生活消费品价格变动趋势", "du": "x"},
    {"_id": CPI_MOM, "i_showname": "居民消费价格指数 (上月=100) ", "i_mark": None, "du": "x"},
]}}

# 2026年10月/9月 are published placeholders (无); 2026年8月 is the newest real print.
ES_DATA = {"success": True, "message": "成功", "data": [
    {"code": "202610MM", "name": "2026年10月", "values": [
        {"_id": CPI_YOY, "value": "无"}, {"_id": CPI_MOM, "value": ""}]},
    {"code": "202609MM", "name": "2026年9月", "values": [
        {"_id": CPI_YOY, "value": "无"}, {"_id": CPI_MOM, "value": "无"}]},
    {"code": "202608MM", "name": "2026年8月", "values": [
        {"_id": CPI_YOY, "value": "100.8"}, {"_id": CPI_MOM, "value": "100.4"}]},
    {"code": "202607MM", "name": "2026年7月", "values": [
        {"_id": CPI_YOY, "value": "100.9"}, {"_id": CPI_MOM, "value": "100.3"}]},
]}


def _fetch(series: str = "CPI", *, catalog=CATALOG, data=ES_DATA, catalog_error=False):
    """Run one fetch against canned HTTP, with a throwaway catalogue cache.

    The session is stubbed rather than ``net`` itself, so the test also pins that the
    adapter goes through the warmed cookie-keeping session (a plain ``net.post_json``
    would be answered with the WAF's intermittent 307). The cache is per-call on purpose:
    a shared cache would let an earlier test's successful catalogue read mask the
    catalogue-failure path.
    """
    (ROOT / "local").mkdir(exist_ok=True)
    scratch = tempfile.mkdtemp(dir=ROOT / "local")

    def fake_post_json(url, payload, **kwargs):
        assert "/stream/esData" in url, url
        assert payload["showType"] == "1", "showType is mandatory upstream"
        assert payload["das"] == [{"text": "全国", "value": "000000000000"}]
        _fetch.last_payload = payload
        return data

    def fake_get_json(url, **kwargs):
        if catalog_error:
            raise net.FetchError("catalog down")
        return catalog

    class StubSession:
        post_json = staticmethod(fake_post_json)
        get_json = staticmethod(fake_get_json)

    _fetch.last_payload = None
    with mock.patch.object(nbs, "_cache_dir", return_value=Path(scratch)), \
            mock.patch.object(nbs, "_session", return_value=StubSession()), \
            mock.patch.object(nbs, "_pause", return_value=0.0):
        return nbs.fetch_macro_nbs(series, as_of="2026-10-02"), _fetch.last_payload


def test_latest_published_period_wins_and_placeholder_values_are_dropped() -> None:
    result, _payload = _fetch()
    metrics = {r.metric: r for r in result.records}
    assert set(metrics) == {"cpi_index_yoy", "cpi_index_mom"}
    latest = metrics["cpi_index_yoy"]
    assert latest.value == 100.8, "无 placeholders must not win or become zero"
    assert latest.period == "2026-08"
    assert latest.unit == "index_yoy_base100"
    assert latest.family == "macro_series" and latest.research_object == "CN_CPI"
    assert latest.period_type == "calendar_period"
    assert metrics["cpi_index_mom"].value == 100.4 and metrics["cpi_index_mom"].period == "2026-08"
    print("ok the newest period with a real value wins and 无 is dropped, not zeroed")


def test_official_l2_posture_and_statistical_basis() -> None:
    result, payload = _fetch()
    record = result.records[0]
    posture = record.posture
    assert posture.source_id == "nbs_stats_api"
    assert posture.source_class == "official_macro_and_industry"
    assert posture.authority_level == "L2", "the statistical agency is the primary source"
    assert posture.claim_type == "fact"
    assert posture.evidence_category == "verified_fact"
    assert record.provenance["vendorMetric"].startswith("居民消费价格指数")
    assert "反映一定时期" in record.provenance["statisticalBasis"]
    assert record.provenance["basisStatus"] == "ok"
    assert record.provenance["catalogId"] == nbs.SERIES["CPI"].cid
    assert record.provenance["indicatorId"] in (CPI_YOY, CPI_MOM)
    assert record.provenance["rootId"] == nbs.ROOTS["monthly"]
    assert "splits one series across catalog ids" in record.provenance["timeSlicing"]
    assert payload["rootId"] == nbs.ROOTS["monthly"]
    assert payload["cid"] == nbs.SERIES["CPI"].cid
    print("ok the official source is stamped L2 and carries its 口径 and catalogue ids")


def test_dts_window_encoding_per_frequency() -> None:
    assert nbs._dts_window("monthly", "2026-10-02") == "202311MM-202610MM"
    assert nbs._dts_window("quarterly", "2026-10-02") == "202301SS-202604SS"
    assert nbs._period_label("202608MM", "monthly") == "2026-08"
    assert nbs._period_label("202604SS", "quarterly") == "2026Q4"
    print("ok dts windows and period labels follow the vendor encoding")


def test_all_placeholder_payload_is_a_source_gap() -> None:
    placeholder_only = {"success": True, "data": [
        {"code": "202610MM", "name": "2026年10月", "values": [{"_id": CPI_YOY, "value": "无"}]}]}
    try:
        _fetch(data=placeholder_only)
    except net.FetchError as exc:
        assert "nbs_source_gap" in str(exc)
    else:
        raise AssertionError("a window with nothing published must be a source gap")

    failure = {"success": False, "message": "参数错误"}
    try:
        _fetch(data=failure)
    except net.FetchError as exc:
        assert "参数错误" in str(exc)
    else:
        raise AssertionError("a vendor failure must surface its message")

    try:
        nbs.fetch_macro_nbs("NOPE")
    except net.FetchError as exc:
        assert "invalid_series" in str(exc)
    else:
        raise AssertionError("an unknown series must be rejected")
    print("ok placeholder-only, vendor failure and unknown series all degrade explicitly")


def test_catalog_failure_degrades_provenance_only() -> None:
    result, _payload = _fetch(catalog_error=True)
    assert result.records, "data must still be emitted when the catalogue call fails"
    assert result.records[0].provenance["basisStatus"] == "catalog_unavailable"
    assert result.records[0].provenance["statisticalBasis"] is None
    print("ok a catalogue failure costs the 口径 note, never the data")


def test_search_and_side_table() -> None:
    with mock.patch.object(nbs, "_session") as session:
        session.return_value.get_json.return_value = {"data": {"list": [
            {"_id": "abc", "catalogid": "cid1", "i_showname": "居民消费价格指数 (上年同月=100) ",
             "i_mark": "口径"},]}}
        rows = nbs.search_indicators("居民消费价格指数")
    assert rows == [{"indicatorId": "abc", "catalogId": "cid1",
                     "name": "居民消费价格指数 (上年同月=100)", "mark": "口径"}]

    single, _ = _fetch()
    assert single.series["name"] == "macro_nbs-CPI"
    assert single.series["columns"] == ["period", "cpi_index_yoy", "cpi_index_mom"]
    assert [row["period"] for row in single.series["rows"]] == ["2026-07", "2026-08"]

    both, _ = _fetch("ALL")
    assert both.series is None, "one side-series slot cannot hold every series"
    print("ok keyword search returns ids and a single series carries its side table")


def test_emitted_rows_and_registry() -> None:
    result, _ = _fetch()
    scratch = ROOT / "local"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as out:
        emitted = emit_bundle(result.records, out_dir=out, research_object="CN_CPI",
                              market_scope="CN", endpoint=nbs.ENDPOINT.format(symbol="CPI"))
        assert emitted["calculation_ledger"] is None
        with open(Path(out) / "evidence-log.csv", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    assert {row["claim_type"] for row in rows} == {"fact"}
    assert {row["authority_level"] for row in rows} == {"L2"}
    assert {row["verification_status"] for row in rows} == {"verified"}
    assert {row["source_id"] for row in rows} == {"nbs_stats_api"}
    assert {row["source_speaker"] for row in rows} == {"official_agency"}
    # The 口径 travels with the metric that has one; a metric the vendor left without a
    # basis note must not be filled in with a placeholder.
    assert any("statisticalBasis=居民消费价格指数反映" in row["notes"] for row in rows)
    assert not any("statisticalBasis=None" in row["notes"] for row in rows)

    with (ROOT / "data" / "source-registry.csv").open(encoding="utf-8", newline="") as fh:
        registry = [row["source_id"] for row in csv.DictReader(fh)]
    with (ROOT / "data" / "source-class-map.csv").open(encoding="utf-8", newline="") as fh:
        mapping = {row["source_id"]: row["source_class"] for row in csv.DictReader(fh)}
    assert registry.count("nbs_stats_api") == 1
    assert mapping["nbs_stats_api"] == "official_macro_and_industry"
    print("ok emitted rows are verified L2 official statistics and the source is registered")


MONEY_CATALOG = {"data": {"list": [
    {"_id": "f3c0ae453a54424489af41de315ec592", "i_showname": "货币和准货币 (M2) 供应量_期末值 (亿元) ",
     "i_mark": "货币和准货币（M2）指流通中现金、单位活期存款、单位定期存款、居民储蓄存款等"},
]}}
MONEY_DATA = {"success": True, "message": "成功", "data": [
    {"code": "202608MM", "name": "2026年8月", "values": [
        {"_id": "f3c0ae453a54424489af41de315ec592", "value": "3568083.6"},
        {"_id": "e03f2232631f41cd9d754a7d7feb4a81", "value": "7.5"},
        {"_id": "add08d4a1ca049158166f126e169edde", "value": "1157741.43"},
        {"_id": "640401d3351b4b868dea28f89f410a54", "value": "4.1"},
        {"_id": "bd67997414b147a08d4aa03d146f4486", "value": "148311.98"},
        {"_id": "db7891fb8f3c4eb2a4d71a9955eba8c7", "value": "11.2"}]},
]}


def test_money_supply_is_an_official_series_not_a_gap() -> None:
    """The agency publishes M0/M1/M2 monthly; an earlier note here wrongly called it a gap."""
    result, _payload = _fetch("MONEY_SUPPLY", catalog=MONEY_CATALOG, data=MONEY_DATA)
    metrics = {r.metric: r for r in result.records}
    assert set(metrics) == {"money_supply_m0", "money_supply_m0_yoy", "money_supply_m1",
                            "money_supply_m1_yoy", "money_supply_m2", "money_supply_m2_yoy"}
    assert metrics["money_supply_m2"].value == 3568083.6
    assert metrics["money_supply_m2"].unit == "CNY_100m"
    assert metrics["money_supply_m2"].currency == "CNY"
    assert metrics["money_supply_m1"].value == 1157741.43
    assert metrics["money_supply_m0"].value == 148311.98
    assert metrics["money_supply_m2_yoy"].unit == "percent"
    assert metrics["money_supply_m2_yoy"].value == 7.5
    assert metrics["money_supply_m2"].period == "2026-08"
    assert metrics["money_supply_m2"].posture.authority_level == "L2"
    assert metrics["money_supply_m2"].posture.source_id == "nbs_stats_api"
    assert metrics["money_supply_m2"].research_object == "CN_MONEY_SUPPLY"
    assert "货币和准货币" in metrics["money_supply_m2"].provenance["vendorMetric"]
    print("ok M0/M1/M2 ship as an official L2 series rather than a declared gap")


def main() -> int:
    test_latest_published_period_wins_and_placeholder_values_are_dropped()
    test_official_l2_posture_and_statistical_basis()
    test_dts_window_encoding_per_frequency()
    test_all_placeholder_payload_is_a_source_gap()
    test_catalog_failure_degrades_provenance_only()
    test_search_and_side_table()
    test_money_supply_is_an_official_series_not_a_gap()
    test_emitted_rows_and_registry()
    print("mira_data_nbs_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
