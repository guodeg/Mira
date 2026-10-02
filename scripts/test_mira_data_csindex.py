#!/usr/bin/env python3
"""Regression tests for the CSI (中证指数) official index adapter.

Offline: HTTP and the workbook parser are patched. The tests pin the things that were learned
by probing the live endpoints and files — the ``YYYYMMDD`` date requirement whose violation
looks like "no data", the positional workbook mapping under a bilingual header, the two
different workbook dates (composition vs weights), the ~21-observation valuation window, and
the optional ``xlrd`` dependency degrading to a labelled gap rather than an ImportError.
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
from tools.mira_data.adapters import csindex_index as csi
from tools.mira_data.emit import emit_bundle


PROFILE = {"code": 200, "data": {
    "indexCode": "000300", "indexFullNameCn": "沪深300指数", "indexFullNameEn": "CSI 300 Index",
    "indexShortNameCn": "沪深300", "basicDate": "20041231", "basicIndex": 1000,
    "consNumber": 300, "currencyCn": "人民币", "indexType": "stock"}}
PERF = {"code": 200, "data": [
    {"tradeDate": "20260929", "indexNameCnAll": "沪深300指数", "open": 4340.0, "high": 4360.0,
     "low": 4330.0, "close": 4345.21, "changePct": -0.1, "tradingValue": 3200.0,
     "tradingVol": 1.5e10, "consNumber": 300.0},
    {"tradeDate": "20260930", "indexNameCnAll": "沪深300指数", "open": 4356.8, "high": 4368.61,
     "low": 4341.88, "close": 4357.62, "changePct": 0.29, "tradingValue": 3564.92,
     "tradingVol": 1.63e10, "consNumber": 300.0}]}
DETAILS = {"code": 200, "data": {
    "样本列表": [{"fileName": "000300cons.xls",
                  "filePath": f"{csi.OSS}/cons/000300cons.xls"}],
    "样本权重": [{"fileName": "000300closeweight.xls",
                  "filePath": f"{csi.OSS}/closeweight/000300closeweight.xls"}],
    "指数估值": [{"fileName": "000300indicator.xls",
                  "filePath": f"{csi.OSS}/indicator/000300indicator.xls"}]}}

CONS_HEADER = ["日期Date", "指数代码 Index Code", "指数名称 Index Name",
               "指数英文名称Index Name(En)", "成份券代码Constituent Code",
               "成份券名称Constituent Name", "成份券英文名称Constituent", "交易所Exchange",
               "交易所英文名称Exchange(En)"]
CONS_ROWS = [CONS_HEADER,
             ["20260930", "000300", "沪深300", "CSI 300", "000001", "平安银行",
              "Ping An Bank Co., Ltd.", "深圳证券交易所", "Shenzhen Stock Exchange"],
             ["20260930", "000300", "沪深300", "CSI 300", "688981", "中芯国际",
              "Semiconductor Manufacturing", "上海证券交易所", "Shanghai Stock Exchange"]]
WEIGHT_ROWS = [CONS_HEADER + ["权重(%)weight"],
               ["20260831", "000300", "沪深300", "CSI 300", "000001", "平安银行",
                "Ping An Bank Co., Ltd.", "深圳证券交易所", "Shenzhen Stock Exchange", "0.433"],
               ["20260831", "000300", "沪深300", "CSI 300", "688981", "中芯国际",
                "Semiconductor Manufacturing", "上海证券交易所", "Shanghai Stock Exchange",
                "0.998"]]
VALUATION_ROWS = [
    ["日期Date", "指数代码Index Code", "指数中文全称Chinese Name", "指数中文简称Index Chinese",
     "指数英文全称English Name", "指数英文简称Index English", "市盈率1（总股本）P/E1",
     "市盈率2（计算用股本）P/E2", "股息率1（总股本）D/P1", "股息率2（计算用股本）D/P2"],
    ["20260930", "000300", "沪深300指数", "沪深300", "CSI 300 Index", "CSI 300",
     "14.34", "16.35", "2.74", "2.49"],
    ["20260903", "000300", "沪深300指数", "沪深300", "CSI 300 Index", "CSI 300",
     "14.87", "17.09", "2.58", "2.3"]]


def _patch(*, profile=PROFILE, perf=PERF, details=DETAILS, rows=WEIGHT_ROWS,
           profile_error=False, parse_error=None):
    def fake_get_json(url, **kwargs):
        if csi.PROFILE_PATH.format(code="000300") in url:
            if profile_error:
                raise net.FetchError("profile down")
            return profile
        if csi.PERF_PATH in url:
            _patch.perf_url = url
            return perf
        if csi.DETAILS_PATH in url:
            return details
        raise AssertionError(f"unexpected url {url}")

    def fake_get(url, **kwargs):
        _patch.file_url = url
        return b"xls-bytes"

    def fake_parse(raw):
        if parse_error:
            raise net.FetchError(parse_error)
        return rows

    return [
        mock.patch.object(csi.net, "get_json", side_effect=fake_get_json),
        mock.patch.object(csi.net, "get", side_effect=fake_get),
        mock.patch.object(csi, "_parse_xls", side_effect=fake_parse),
    ]


def _run(function, *args, patch_kwargs=None, **kwargs):
    patchers = _patch(**(patch_kwargs or {}))
    for patcher in patchers:
        patcher.start()
    try:
        return function(*args, **kwargs)
    finally:
        for patcher in patchers:
            patcher.stop()


def test_benchmark_requests_yyyymmdd_and_emits_close_and_member_count() -> None:
    result = _run(csi.fetch_index_benchmark, "000300", as_of="2026-10-02")
    assert "startDate=" in _patch.perf_url
    start = _patch.perf_url.split("startDate=")[1].split("&")[0]
    end = _patch.perf_url.split("endDate=")[1]
    assert start.isdigit() and len(start) == 8 and end == "20261002", _patch.perf_url

    metrics = {r.metric: r for r in result.records}
    assert set(metrics) == {"index_close", "index_member_count"}
    close = metrics["index_close"]
    assert close.family == "market_price" and close.value == 4357.62
    assert close.unit == "index_points" and close.research_object == "000300.CSI"
    assert close.period == "2026-09-30" and close.source_date == "2026-09-30"
    posture = close.posture
    assert posture.source_id == "csindex_index_api"
    assert posture.authority_level == "L2", "the compiler is authoritative for its own index"
    assert posture.claim_type == "market_pricing"
    assert close.provenance["profileStatus"] == "ok"
    assert close.provenance["tradeDate"] == "20260930"
    assert close.provenance["changePct"] == 0.29
    assert metrics["index_member_count"].value == 300.0

    series = result.series
    assert series["name"] == "index_benchmark-000300"
    assert [row["date"] for row in series["rows"]] == ["2026-09-29", "2026-09-30"]
    assert series["rows"][-1]["close"] == 4357.62
    print("ok the benchmark read uses YYYYMMDD and emits the official close plus member count")


def test_profile_failure_costs_only_the_profile_and_empty_history_is_a_named_gap() -> None:
    result = _run(csi.fetch_index_benchmark, "000300", as_of="2026-10-02", patch_kwargs={"profile_error": True})
    assert result.records[0].provenance["profileStatus"] == "profile_unavailable"
    assert result.records[0].value == 4357.62

    try:
        _run(csi.fetch_index_benchmark, "000300", as_of="2026-10-02",
             patch_kwargs={"perf": {"code": 200, "data": []}})
    except net.FetchError as exc:
        assert "csindex_source_gap" in str(exc) and "YYYYMMDD" in str(exc)
    else:
        raise AssertionError("an empty history must be a gap that names the date format")
    print("ok a profile failure only costs the profile, and empty history names the cause")


def test_members_map_positionally_and_record_both_workbook_dates() -> None:
    result = _run(csi.fetch_index_members, "000300", as_of="2026-10-02")
    assert _patch.file_url.endswith("000300closeweight.xls")
    weights = {r.provenance["constituentCode"]: r for r in result.records}
    assert set(weights) == {"000001", "688981"}, weights.keys()
    first = weights["000001"]
    assert first.metric == "index_member_weight" and first.value == 0.433
    assert first.unit == "percent" and first.research_object == "000300.CSI"
    assert first.period == "2026-08-31", "the weight workbook date is the period"
    assert first.provenance["constituentName"] == "平安银行"
    assert "different dates" in first.provenance["compositionCaveat"]
    assert first.provenance["workbookKind"] == "weights"
    assert first.provenance["workbookUrl"].endswith("000300closeweight.xls")

    series = result.series
    assert series["columns"] == ["date", "code", "name", "exchange", "weight"]
    assert series["rows"][1]["weight"] == 0.998

    counted = _run(csi.fetch_index_members, "000300", as_of="2026-10-02", with_weights=False, patch_kwargs={"rows": CONS_ROWS})
    assert _patch.file_url.endswith("000300cons.xls")
    assert [r.metric for r in counted.records] == ["index_member_count"]
    assert counted.records[0].value == 2.0 and counted.records[0].period == "2026-09-30"
    print("ok members map positionally, keep the workbook date, and fall back to a count")


def test_valuation_is_the_first_adapter_for_the_family_and_records_its_window() -> None:
    result = _run(csi.fetch_index_valuation, "000300", as_of="2026-10-02", patch_kwargs={"rows": VALUATION_ROWS})
    metrics = {r.metric: r for r in result.records}
    assert set(metrics) == {"index_pe_total", "index_pe_calculated",
                            "index_dividend_yield_total", "index_dividend_yield_calculated"}
    assert metrics["index_pe_total"].value == 14.34
    assert metrics["index_pe_total"].unit == "ratio"
    assert metrics["index_dividend_yield_total"].value == 2.74
    assert metrics["index_dividend_yield_total"].unit == "percent"
    record = metrics["index_pe_total"]
    assert record.family == "valuation_snapshot"
    assert record.period == "2026-09-30", "the newest row in the workbook wins"
    assert record.posture.authority_level == "L2"
    assert "~21 observations" in record.provenance["windowCaveat"]
    assert "计算用股本" in record.provenance["ratioBasis"]
    assert [row["date"] for row in result.series["rows"]] == ["2026-09-03", "2026-09-30"]
    print("ok index valuation emits the compiler's ratios with its short-window caveat")


def test_missing_xlrd_degrades_to_a_labelled_dependency_gap() -> None:
    with mock.patch.object(csi, "_require_xlrd",
                           side_effect=net.FetchError("csindex_dependency_gap: install xlrd")):
        try:
            csi._parse_xls(b"not really a workbook")
        except net.FetchError as exc:
            assert "csindex_dependency_gap" in str(exc) and "xlrd" in str(exc)
        else:
            raise AssertionError("a missing xlrd must be a labelled dependency gap")

    try:
        _run(csi.fetch_index_members, "000300", as_of="2026-10-02",
             patch_kwargs={"parse_error": "csindex_dependency_gap: install the optional dependency 'xlrd'"})
    except net.FetchError as exc:
        assert "csindex_dependency_gap" in str(exc)
    else:
        raise AssertionError("the workbook path must surface the dependency gap")
    print("ok a missing xlrd is a labelled gap on both the parser and the family path")


def test_codes_and_helpers() -> None:
    assert csi._normalise_code("000300") == "000300"
    assert csi._normalise_code("000300.SH") == "000300"
    assert csi._normalise_code("h30374") == "H30374"
    for bad in ("", "0003000000000", "沪深300!"):
        try:
            csi._normalise_code(bad)
        except net.FetchError as exc:
            assert "invalid_index_code" in str(exc)
        else:
            raise AssertionError(f"expected invalid_index_code for {bad!r}")
    assert csi._member_code(1.0) == "000001"
    assert csi._member_code("688981") == "688981"
    assert csi._iso_date("20260930") == "2026-09-30"
    assert csi._number("2.74") == 2.74 and csi._number("") is None
    print("ok index codes, member-code padding and date normalisation")


def test_emitted_rows_and_registry() -> None:
    result = _run(csi.fetch_index_benchmark, "000300", as_of="2026-10-02")
    scratch = ROOT / "local"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as out:
        emitted = emit_bundle(result.records, out_dir=out, research_object="000300.CSI",
                              market_scope="CN", endpoint=csi.ENDPOINT.format(symbol="000300"))
        assert emitted["calculation_ledger"] is None
        with open(Path(out) / "evidence-log.csv", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    assert {row["claim_type"] for row in rows} == {"market_pricing"}
    assert {row["authority_level"] for row in rows} == {"L2"}
    assert {row["verification_status"] for row in rows} == {"verified"}
    assert {row["source_id"] for row in rows} == {"csindex_index_api"}
    assert {row["source_speaker"] for row in rows} == {"official_agency"}

    with (ROOT / "data" / "source-registry.csv").open(encoding="utf-8", newline="") as fh:
        registry = [row for row in csv.DictReader(fh)]
    rows = [row for row in registry if row["source_id"] == "csindex_index_api"]
    assert len(rows) == 1 and rows[0]["authority_level"] == "L2"
    assert "xlrd" in rows[0]["notes"] and "YYYYMMDD" in rows[0]["notes"]
    with (ROOT / "data" / "source-class-map.csv").open(encoding="utf-8", newline="") as fh:
        mapping = {row["source_id"]: row for row in csv.DictReader(fh)}
    assert mapping["csindex_index_api"]["source_class"] == "market_price_and_trading"
    assert "overridden" in mapping["csindex_index_api"]["notes"]
    print("ok index rows emit as verified L2 market pricing with an explained tier override")


def main() -> int:
    test_benchmark_requests_yyyymmdd_and_emits_close_and_member_count()
    test_profile_failure_costs_only_the_profile_and_empty_history_is_a_named_gap()
    test_members_map_positionally_and_record_both_workbook_dates()
    test_valuation_is_the_first_adapter_for_the_family_and_records_its_window()
    test_missing_xlrd_degrades_to_a_labelled_dependency_gap()
    test_codes_and_helpers()
    test_emitted_rows_and_registry()
    print("mira_data_csindex_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


