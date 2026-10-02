#!/usr/bin/env python3
"""Regression tests for the CFETS benchmark-rate adapter (LPR / Shibor).

Offline: the HTTP layer is patched. The tests pin the things that would otherwise go
wrong quietly — the vendor's display-string dates (which must be parsed, not trusted by
position), percent values kept as published, the monthly-vs-daily frequencies, the side
series only being attached for a single benchmark, and the L2 official posture.
"""

from __future__ import annotations

import csv
import datetime as _dt
import sys
import tempfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import net
from tools.mira_data.adapters import chinamoney_rates as cr
from tools.mira_data.emit import emit_bundle


LPR = {"head": {"rep_code": "200"},
       "data": {"startDateCN": "2025-10-03", "endDateCN": "2026-10-02", "message": ""},
       "records": [
           # Deliberately oldest-first to prove ordering comes from the parsed date.
           {"1Y": "3.10", "5Y": "3.60", "showDateCN": "2026-07-20"},
           {"1Y": "3.05", "5Y": "3.55", "showDateCN": "2026-08-20"},
           {"1Y": "3.00", "5Y": "3.50", "showDateCN": "2026-09-20"},
       ]}

SHIBOR = {"head": {"rep_code": "200"}, "data": {},
          "records": [
              {"ON": "1.3451", "1W": "1.3830", "2W": "1.3839", "1M": "1.4237", "3M": "1.4300",
               "6M": "1.4500", "9M": "1.4700", "1Y": "1.4800", "showDateCN": "2026-09-29"},
              {"ON": "1.3620", "1W": "1.3710", "2W": "1.3980", "1M": "1.4210", "3M": "1.4300",
               "6M": "1.4500", "9M": "1.4700", "1Y": "1.4800", "showDateCN": "2026-09-30"},
          ]}


def _fetch(benchmark: str, payload_lpr=LPR, payload_shibor=SHIBOR):
    def fake_get_json(url, **kwargs):
        return payload_lpr if "LprHis" in url else payload_shibor

    with mock.patch.object(cr.net, "get_json", side_effect=fake_get_json):
        return cr.fetch_macro_rates(benchmark, as_of="2026-10-02")


def test_lpr_claims_use_the_newest_parsed_date() -> None:
    result = _fetch("LPR")
    metrics = {r.metric: r for r in result.records}
    assert set(metrics) == {"lpr_1y", "lpr_5y"}
    assert metrics["lpr_1y"].value == 3.00 and metrics["lpr_5y"].value == 3.50
    assert metrics["lpr_1y"].period == "2026-09-20", "the newest record must win by date"
    assert metrics["lpr_1y"].source_date == "2026-09-20"
    assert metrics["lpr_1y"].unit == "percent"
    assert metrics["lpr_1y"].family == "macro_series"
    assert metrics["lpr_1y"].research_object == "CN_LPR"
    assert metrics["lpr_1y"].posture.source_id == "chinamoney_rates_api"
    assert metrics["lpr_1y"].posture.source_class == "official_macro_and_industry"
    assert metrics["lpr_1y"].posture.authority_level == "L2"
    assert metrics["lpr_1y"].provenance["frequency"] == "monthly"
    assert metrics["lpr_1y"].provenance["observations"] == 3
    print("ok LPR claims come from the newest parsed date, in percent, as L2 macro")


def test_shibor_tenors_and_side_series() -> None:
    result = _fetch("SHIBOR")
    metrics = {r.metric for r in result.records}
    assert metrics == {"shibor_on", "shibor_1w", "shibor_2w", "shibor_1m", "shibor_3m",
                       "shibor_6m", "shibor_9m", "shibor_1y"}
    on = next(r for r in result.records if r.metric == "shibor_on")
    assert on.value == 1.3620 and on.period == "2026-09-30"
    assert on.provenance["frequency"] == "daily"

    series = result.series
    assert series is not None and series["name"] == "macro_rates-SHIBOR"
    assert series["columns"][0] == "date" and "shibor_1y" in series["columns"]
    assert [row["date"] for row in series["rows"]] == ["2026-09-29", "2026-09-30"]
    print("ok Shibor emits every tenor and attaches the window as a bulk series")


def test_both_benchmarks_skip_the_side_series() -> None:
    result = _fetch("BOTH")
    assert {r.research_object for r in result.records} == {"CN_LPR", "CN_SHIBOR"}
    assert result.series is None, "one side-series slot cannot hold two benchmarks"
    print("ok a BOTH read emits claims for both benchmarks without a side series")


def test_undated_rows_are_dropped_and_empty_is_a_gap() -> None:
    undated = {"records": [{"1Y": "3.00", "5Y": "3.50", "showDateCN": "not a date"}]}
    try:
        _fetch("LPR", payload_lpr=undated)
    except net.FetchError as exc:
        assert "rates_source_gap" in str(exc)
    else:
        raise AssertionError("rows without a parsable date must not become claims")

    try:
        _fetch("LPR", payload_lpr={"records": []})
    except net.FetchError as exc:
        assert "rates_source_gap" in str(exc)
    else:
        raise AssertionError("an empty payload must degrade to a source gap")

    try:
        cr.fetch_macro_rates("LIBOR")
    except net.FetchError as exc:
        assert "invalid_benchmark" in str(exc)
    else:
        raise AssertionError("an unknown benchmark must be rejected")
    print("ok undated or empty payloads degrade, and unknown benchmarks are rejected")


def test_window_is_clamped_to_the_vendor_one_year_limit() -> None:
    """The vendor answers HTTP 200 + empty rows when the window exceeds a year."""
    import os

    captured = {}

    def fake_get_json(url, **kwargs):
        captured["url"] = url
        return LPR

    with mock.patch.dict(os.environ, {"MIRA_RATES_WINDOW_DAYS": "400"}, clear=False), \
            mock.patch.object(cr.net, "get_json", side_effect=fake_get_json):
        cr.config.reset_cache()
        cr.fetch_macro_rates("LPR", as_of="2026-10-02")
    cr.config.reset_cache()
    start = captured["url"].split("startDate=")[1].split("&")[0]
    end = captured["url"].split("endDate=")[1]
    span = (_dt.date.fromisoformat(end) - _dt.date.fromisoformat(start)).days
    assert span <= 365, f"request window must fit the vendor limit, got {span} days"

    over = {"data": {"message": "只提供一年历史数据查询及下载"}, "records": []}
    try:
        _fetch("SHIBOR", payload_shibor=over)
    except net.FetchError as exc:
        assert "只提供一年历史数据查询及下载" in str(exc), str(exc)
    else:
        raise AssertionError("an over-long window must surface the vendor message")
    print("ok the request window is clamped and the vendor message reaches the error")


def test_emitted_rows_are_l2_official_facts() -> None:
    result = _fetch("LPR")
    scratch = ROOT / "local"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as out:
        emitted = emit_bundle(result.records, out_dir=out, research_object="CN_LPR",
                              market_scope="CN", endpoint=cr.LPR_ENDPOINT.format(symbol="LPR"))
        assert emitted["calculation_ledger"] is None
        with open(Path(out) / "evidence-log.csv", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    assert {row["claim_type"] for row in rows} == {"fact"}
    assert {row["authority_level"] for row in rows} == {"L2"}
    assert {row["verification_status"] for row in rows} == {"verified"}
    assert {row["source_speaker"] for row in rows} == {"official_agency"}
    assert {row["source_id"] for row in rows} == {"chinamoney_rates_api"}
    assert all("vendorUnit=percent" in row["notes"] for row in rows)
    print("ok emitted rate rows are verified L2 official statistics")


def test_registry_rows_are_registered() -> None:
    with (ROOT / "data" / "source-registry.csv").open(encoding="utf-8", newline="") as fh:
        registry = [row["source_id"] for row in csv.DictReader(fh)]
    with (ROOT / "data" / "source-class-map.csv").open(encoding="utf-8", newline="") as fh:
        mapping = {row["source_id"]: row["source_class"] for row in csv.DictReader(fh)}
    assert registry.count("chinamoney_rates_api") == 1
    assert mapping["chinamoney_rates_api"] == "official_macro_and_industry"
    print("ok the CFETS rate source is registered with a 1:1 class-map row")


def main() -> int:
    test_lpr_claims_use_the_newest_parsed_date()
    test_shibor_tenors_and_side_series()
    test_both_benchmarks_skip_the_side_series()
    test_undated_rows_are_dropped_and_empty_is_a_gap()
    test_window_is_clamped_to_the_vendor_one_year_limit()
    test_emitted_rows_are_l2_official_facts()
    test_registry_rows_are_registered()
    print("mira_data_rates_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
