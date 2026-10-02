#!/usr/bin/env python3
"""Regression tests for the national-accounts relay adapter (CPI/PPI/GDP/PMI).

Offline: HTTP is patched. The tests pin the tier honesty (an aggregator relay must not be
dressed up as L2), the period labels derived from the vendor's display strings, the
quarterly GDP case, the absence of a publish timestamp, and the documented money-supply gap.
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
from tools.mira_data.adapters import eastmoney_macro as em
from tools.mira_data.emit import emit_bundle


CPI = {"success": True, "result": {"data": [
    {"REPORT_DATE": "2026-08-01 00:00:00", "TIME": "2026年08月份", "NATIONAL_SAME": 0.8,
     "NATIONAL_SEQUENTIAL": 0.4, "NATIONAL_BASE": 100.8},
    {"REPORT_DATE": "2026-07-01 00:00:00", "TIME": "2026年07月份", "NATIONAL_SAME": 0.9,
     "NATIONAL_SEQUENTIAL": 0.3, "NATIONAL_BASE": 100.9},
]}}
PPI = {"success": True, "result": {"data": [
    {"REPORT_DATE": "2026-08-01 00:00:00", "TIME": "2026年08月份", "BASE": 103.8,
     "BASE_SAME": 3.8, "BASE_ACCUMULATE": 102},
]}}
GDP = {"success": True, "result": {"data": [
    {"REPORT_DATE": "2026-06-01 00:00:00", "TIME": "2026年第1-2季度",
     "DOMESTICL_PRODUCT_BASE": 695704, "SUM_SAME": 4.7, "FIRST_SAME": 3.7},
]}}
PMI = {"success": True, "result": {"data": [
    {"REPORT_DATE": "2026-09-01 00:00:00", "TIME": "2026年09月份", "MAKE_INDEX": 50.1,
     "MAKE_SAME": 0.60240964, "NMAKE_INDEX": 50.2, "NMAKE_SAME": 0.4},
]}}
BY_TABLE = {"RPT_ECONOMY_CPI": CPI, "RPT_ECONOMY_PPI": PPI,
            "RPT_ECONOMY_GDP": GDP, "RPT_ECONOMY_PMI": PMI}


def _fetch(series: str):
    def fake_get_json(url, **kwargs):
        for table, payload in BY_TABLE.items():
            if table in url:
                return payload
        raise AssertionError(f"unexpected url: {url}")

    with mock.patch.object(em.net, "get_json", side_effect=fake_get_json):
        return em.fetch_macro_china(series, as_of="2026-10-02")


def test_cpi_is_a_relay_not_an_official_l2() -> None:
    result = _fetch("CPI")
    metrics = {r.metric: r for r in result.records}
    assert set(metrics) == {"cpi_yoy", "cpi_mom", "cpi_index"}
    latest = metrics["cpi_yoy"]
    assert latest.value == 0.8 and latest.unit == "percent"
    assert latest.period == "2026-08" and latest.period_type == "calendar_period"
    assert latest.family == "macro_series" and latest.research_object == "CN_CPI"
    posture = latest.posture
    assert posture.source_id == "eastmoney_macro_api"
    assert posture.authority_level == "L5", "an aggregator relay must not claim L2"
    assert posture.claim_type == "reported_metric"
    assert posture.evidence_category == "reported_fact"
    assert "no publish timestamp" in latest.provenance["vintage"]
    assert latest.provenance["vendorPeriodLabel"] == "2026年08月份"
    assert "anti-bot" in latest.provenance["authorityNote"]
    print("ok CPI is emitted as an L5 relay with the vintage and authority caveats")


def test_period_labels_and_quarterly_gdp() -> None:
    gdp = {r.metric: r for r in _fetch("GDP").records}
    assert gdp["gdp_yoy"].value == 4.7
    assert gdp["gdp_ytd"].value == 695704 and gdp["gdp_ytd"].unit == "CNY_100m"
    assert gdp["gdp_ytd"].currency == "CNY"
    assert gdp["gdp_yoy"].period == "2026Q2", gdp["gdp_yoy"].period
    assert gdp["gdp_yoy"].provenance["frequency"] == "quarterly"

    assert em._period_label("CPI", {"TIME": "2026年08月份"}) == "2026-08"
    assert em._period_label("CPI", {"TIME": "junk", "REPORT_DATE": "2026-08-01 00:00:00"}) == "2026-08"
    assert em._period_label("GDP", {"TIME": "2027年第1-3季度", "REPORT_DATE": "2027-09-30"}) == "2027Q3"
    assert em._period_label("GDP", {"TIME": "junk", "REPORT_DATE": "2027-09-30"}) == "2027Q3"
    print("ok monthly labels come from TIME and GDP labels from the reported quarter")


def test_all_series_and_the_money_supply_gap() -> None:
    result = _fetch("ALL")
    objects = {r.research_object for r in result.records}
    assert objects == {"CN_CPI", "CN_PPI", "CN_GDP", "CN_PMI"}
    assert result.series is None, "one side-series slot cannot hold four series"

    single = _fetch("PMI")
    assert single.series["name"] == "macro_china-PMI"
    assert single.series["columns"] == ["period", "pmi_manufacturing", "pmi_non_manufacturing"]
    assert single.series["rows"][-1]["period"] == "2026-09"

    # Money supply has no vendor table; it must stay an explicit gap, not a silent zero.
    assert "MONEY_SUPPLY" not in em.SERIES
    try:
        em.fetch_macro_china("M2")
    except net.FetchError as exc:
        assert "invalid_series" in str(exc)
    else:
        raise AssertionError("an unknown series must be rejected")
    print("ok ALL emits every series, one series carries a side table, money supply stays out")


def test_empty_table_is_a_source_gap_with_the_vendor_message() -> None:
    empty = {"success": False, "message": "报表配置不存在", "result": None}
    with mock.patch.object(em.net, "get_json", return_value=empty):
        try:
            em.fetch_macro_china("CPI", as_of="2026-10-02")
        except net.FetchError as exc:
            assert "macro_source_gap" in str(exc) and "报表配置不存在" in str(exc)
        else:
            raise AssertionError("an empty vendor table must degrade to a source gap")
    print("ok an empty vendor table surfaces the vendor message as a source gap")


def test_emitted_rows_state_they_are_a_relay() -> None:
    result = _fetch("CPI")
    scratch = ROOT / "local"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as out:
        emitted = emit_bundle(result.records, out_dir=out, research_object="CN_CPI",
                              market_scope="CN", endpoint=em.ENDPOINT.format(symbol="CPI"))
        assert emitted["calculation_ledger"] is None
        with open(Path(out) / "evidence-log.csv", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    assert {row["claim_type"] for row in rows} == {"reported_metric"}
    assert {row["authority_level"] for row in rows} == {"L5"}
    assert {row["source_id"] for row in rows} == {"eastmoney_macro_api"}
    assert all("authorityNote=" in row["notes"] for row in rows)
    assert all("vintage=" in row["notes"] for row in rows)
    print("ok emitted macro rows carry their relay status into the evidence log")


def test_registry_rows_are_registered() -> None:
    with (ROOT / "data" / "source-registry.csv").open(encoding="utf-8", newline="") as fh:
        registry = [row["source_id"] for row in csv.DictReader(fh)]
    with (ROOT / "data" / "source-class-map.csv").open(encoding="utf-8", newline="") as fh:
        mapping = {row["source_id"]: row["source_class"] for row in csv.DictReader(fh)}
    assert registry.count("eastmoney_macro_api") == 1
    assert mapping["eastmoney_macro_api"] == "official_macro_and_industry"
    print("ok the national-accounts relay is registered with a 1:1 class-map row")


def main() -> int:
    test_cpi_is_a_relay_not_an_official_l2()
    test_period_labels_and_quarterly_gdp()
    test_all_series_and_the_money_supply_gap()
    test_empty_table_is_a_source_gap_with_the_vendor_message()
    test_emitted_rows_state_they_are_a_relay()
    test_registry_rows_are_registered()
    print("mira_data_macro_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
