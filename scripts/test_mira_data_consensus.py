#!/usr/bin/env python3
"""Regression tests for the Eastmoney consensus (expectation baseline) adapter.

Offline: the HTTP layer is patched, so no network. The tests pin the decisions that
are easy to get wrong — the year-mark parsing that separates the reported year from
estimate years, the forecast/estimate posture (never a reported fact), the missing
as-of timestamp, and null rating tallies that must not become zeros.
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
from tools.mira_data.adapters import eastmoney_consensus as em
from tools.mira_data.canonical import POSTURES
from tools.mira_data.emit import emit_bundle


ROW = {
    "SECUCODE": "600519.SH", "SECURITY_CODE": "600519", "SECURITY_NAME_ABBR": "贵州茅台",
    "RATING_ORG_NUM": 43, "RATING_BUY_NUM": 34, "RATING_ADD_NUM": 9,
    "RATING_NEUTRAL_NUM": None, "RATING_REDUCE_NUM": None, "RATING_SALE_NUM": None,
    "RATING_LONG_NUM": 43,
    "YEAR1": 2025, "YEAR_MARK1": "A", "EPS1": 65.851754826108,
    "YEAR2": 2026, "YEAR_MARK2": "E", "EPS2": 67.249318181818,
    "YEAR3": 2027, "YEAR_MARK3": "E", "EPS3": 71.125909090909,
    "YEAR4": 2028, "YEAR_MARK4": "E", "EPS4": 75.18,
    "INDUSTRY_BOARD": "白酒Ⅱ", "DEC_AIMPRICEMAX": 2030, "DEC_AIMPRICEMIN": 1430,
}


def _envelope(row=ROW, *, success=True):
    return {"success": success, "code": 0 if success else 500,
            "message": "ok" if success else "server busy",
            "result": {"pages": 1, "count": 1 if row else 0,
                       "data": [row] if row else []}}


def _fetch(**kwargs):
    with mock.patch.object(em.net, "get_json", return_value=_envelope()):
        return em.fetch_consensus("600519", as_of="2026-10-02", **kwargs)


def test_only_estimate_years_become_claims() -> None:
    records = _fetch().records
    eps = {r.period: r for r in records if r.metric == "consensus_eps"}
    assert set(eps) == {"FY2026E", "FY2027E", "FY2028E"}
    assert eps["FY2026E"].value == 67.249318181818
    assert eps["FY2028E"].value == 75.18
    # The A-marked year is the reported base, not an estimate: it must not be claimed.
    assert all(r.value != 65.851754826108 for r in records)
    assert eps["FY2026E"].provenance["forecastBase"] == "FY2025A=65.851754826108"
    assert eps["FY2026E"].provenance["yearMark"] == "E"
    print("ok only E-marked years become consensus claims; the A year is carried as the base")


def test_units_periods_and_claim_types() -> None:
    records = {r.metric: r for r in _fetch().records}
    assert records["consensus_eps"].unit == "CNY/share"
    assert records["consensus_eps"].period_type == "forward"
    assert records["consensus_eps"].currency == "CNY"
    assert records["target_price_high"].value == 2030
    assert records["target_price_high"].unit == "CNY"
    assert records["target_price_low"].value == 1430
    assert records["rating_report_count_6m"].unit == "count"
    assert records["rating_report_count_6m"].period_type == "point_in_time"

    posture = records["consensus_eps"].posture
    assert posture.source_id == "eastmoney_consensus_api"
    assert posture.authority_level == "L5"
    assert posture.claim_type == "forecast"
    assert posture.evidence_category == "estimate"
    assert records["consensus_eps"].research_object == "600519.SH"
    assert records["consensus_eps"].market_scope == "CN"
    print("ok forecast/estimate posture, units and forward periods are right")


def test_null_rating_counts_are_skipped_not_zeroed() -> None:
    metrics = {r.metric for r in _fetch().records}
    assert {"rating_report_count_6m", "rating_buy_count_6m", "rating_add_count_6m"} <= metrics
    # The vendor returns null for these; emitting 0 would invent analyst coverage.
    assert not {"rating_neutral_count_6m", "rating_reduce_count_6m",
                "rating_sale_count_6m"} & metrics
    print("ok null rating tallies are skipped rather than turned into zeros")


def test_vintage_caveat_travels_with_every_record() -> None:
    records = _fetch().records
    assert records
    for record in records:
        assert record.source_date == "2026-10-02"
        assert "no as-of timestamp" in record.provenance["vintage"]
    scratch = ROOT / "local"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as out:
        emitted = emit_bundle(records, out_dir=out, research_object="600519.SH",
                              market_scope="CN", endpoint=em.ENDPOINT.format(thscode="600519.SH"))
        assert emitted["calculation_ledger"] is None    # consensus is disclosed, not derived
        with open(Path(out) / "evidence-log.csv", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    assert {row["claim_type"] for row in rows} == {"forecast"}
    assert {row["verification_status"] for row in rows} == {"unverified"}
    assert {row["source_speaker"] for row in rows} == {"sellside"}
    assert {row["authority_level"] for row in rows} == {"L5"}
    assert all("vintage=" in row["notes"] for row in rows)
    print("ok forecast rows emit as unverified sell-side expectations with the vintage note")


def test_query_is_single_name_only() -> None:
    captured = {}

    def fake_get_json(url, **kwargs):
        captured["url"] = url
        return _envelope()

    with mock.patch.object(em.net, "get_json", side_effect=fake_get_json):
        em.fetch_consensus("600519.SH", as_of="2026-10-02")
    url = captured["url"]
    assert "reportName=RPT_WEB_RESPREDICT" in url
    assert "SECURITY_CODE%3D%22600519%22" in url or 'SECURITY_CODE="600519"' in url
    assert "INDUSTRY_BOARD" not in url          # never widen to an industry or the market
    print("ok the request is filtered to one security and never widens to a scan")


def test_missing_row_is_a_source_gap() -> None:
    with mock.patch.object(em.net, "get_json", return_value=_envelope(None)):
        try:
            em.fetch_consensus("600519", as_of="2026-10-02")
        except net.FetchError as exc:
            assert "eastmoney_source_gap" in str(exc)
        else:
            raise AssertionError("expected a source gap for a name with no coverage")
    print("ok a name without sell-side coverage degrades to a source gap")


def test_failed_envelope_is_an_error() -> None:
    with mock.patch.object(em.net, "get_json", return_value=_envelope(success=False)):
        try:
            em.fetch_consensus("600519", as_of="2026-10-02")
        except net.FetchError as exc:
            assert "eastmoney_error" in str(exc) and "server busy" in str(exc)
        else:
            raise AssertionError("expected an error for success=false")
    print("ok a success=false envelope becomes a labelled source gap")


def test_no_coverage_envelope_is_a_source_gap_not_an_error() -> None:
    """The vendor reports "no coverage" as success=false + 返回数据为空."""
    envelope = {"success": False, "code": 9201, "message": "返回数据为空", "result": None}
    with mock.patch.object(em.net, "get_json", return_value=envelope):
        try:
            em.fetch_consensus("920675", as_of="2026-10-02")
        except net.FetchError as exc:
            assert "eastmoney_source_gap" in str(exc), str(exc)
            assert "9201" not in str(exc) or "返回数据为空" in str(exc)
        else:
            raise AssertionError("expected a source gap for an uncovered name")
    print("ok vendor 'no data' envelopes are source gaps, not errors")


def test_registry_rows_are_registered() -> None:
    with (ROOT / "data" / "source-registry.csv").open(encoding="utf-8", newline="") as fh:
        registry = [row["source_id"] for row in csv.DictReader(fh)]
    with (ROOT / "data" / "source-class-map.csv").open(encoding="utf-8", newline="") as fh:
        mapping = {row["source_id"]: row["source_class"] for row in csv.DictReader(fh)}
    assert "eastmoney_consensus_api" in registry
    assert registry.count("eastmoney_consensus_api") == 1
    assert mapping["eastmoney_consensus_api"] == "consensus_and_estimates"
    assert POSTURES["eastmoney_consensus"].source_id in mapping
    print("ok the consensus source is registered with a 1:1 class-map row")


def main() -> int:
    test_only_estimate_years_become_claims()
    test_units_periods_and_claim_types()
    test_null_rating_counts_are_skipped_not_zeroed()
    test_vintage_caveat_travels_with_every_record()
    test_query_is_single_name_only()
    test_missing_row_is_a_source_gap()
    test_failed_envelope_is_an_error()
    test_no_coverage_envelope_is_a_source_gap_not_an_error()
    test_registry_rows_are_registered()
    print("mira_data_consensus_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
