#!/usr/bin/env python3
"""Regression tests for the SEC companyfacts cash-flow span handling.

Issuers file Q2/Q3 cash flow as 6-/9-month year-to-date spans, so the naive
"latest duration row" is wrong twice over: as a quarter it is mislabeled, and the
safe alternative (last clean quarter) silently lags the income statement by two
quarters. These tests pin both the default behaviour and the ``ytd``
reconciliation, plus the period-basis columns screening now exposes.

Everything here is offline: companyfacts payloads are fixtures.
"""

from __future__ import annotations

import csv
import sys
import tempfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import screening
from tools.mira_data.adapters import sec_companyfacts as scf
from tools.mira_data.emit import emit_bundle


def _row(tag, start, end, val, *, fy, fp, form="10-Q", filed, accn="0000320193-26-000020"):
    row = {"end": end, "val": val, "fy": fy, "fp": fp, "form": form,
           "filed": filed, "accn": accn}
    if start:
        row["start"] = start
    return row


def _tagblock(tag, rows, unit="USD"):
    return {"_tag": tag, "units": {unit: rows}}


def _ocf_block() -> dict:
    return _tagblock("NetCashProvidedByUsedInOperatingActivities", [
        _row(None, "2024-09-29", "2025-09-27", 118000000000, fy=2025, fp="FY",
             form="10-K", filed="2025-10-31"),
        _row(None, "2025-09-28", "2025-12-27", 53925000000, fy=2026, fp="Q1",
             filed="2026-01-30"),
        _row(None, "2025-09-28", "2026-03-28", 82000000000, fy=2026, fp="Q2",
             filed="2026-05-01"),
        _row(None, "2025-09-28", "2026-06-27", 105000000000, fy=2026, fp="Q3",
             filed="2026-07-31"),
    ])


def _capex_block() -> dict:
    return _tagblock("PaymentsToAcquirePropertyPlantAndEquipment", [
        _row(None, "2024-09-29", "2025-09-27", 12000000000, fy=2025, fp="FY",
             form="10-K", filed="2025-10-31"),
        _row(None, "2025-09-28", "2025-12-27", 2373000000, fy=2026, fp="Q1",
             filed="2026-01-30"),
        _row(None, "2025-09-28", "2026-06-27", 8500000000, fy=2026, fp="Q3",
             filed="2026-07-31"),
    ])


def _facts() -> dict:
    return {
        "us-gaap": {
            "NetCashProvidedByUsedInOperatingActivities": {"units": _ocf_block()["units"]},
            "PaymentsToAcquirePropertyPlantAndEquipment": {"units": _capex_block()["units"]},
            "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
                _row(None, "2025-03-30", "2025-06-28", 94036000000, fy=2025, fp="Q3",
                     filed="2025-08-01"),
                _row(None, "2026-03-29", "2026-06-27", 109417000000, fy=2026, fp="Q3",
                     filed="2026-07-31"),
            ]}},
            "Assets": {"units": {"USD": [
                _row(None, None, "2026-06-27", 383266000000, fy=2026, fp="Q3",
                     filed="2026-07-31"),
            ]}},
        },
        "dei": {
            "EntityCommonStockSharesOutstanding": {"units": {"shares": [
                _row(None, None, "2026-07-17", 14594180000, fy=2026, fp="Q3",
                     filed="2026-07-31"),
            ]}},
        },
    }


def test_quarter_span_keeps_existing_behaviour() -> None:
    selection = scf._select_metric_observation(
        _ocf_block(), "duration", "operating_cash_flow", cash_flow_span="quarter")
    assert selection is not None
    assert selection.conversion == "single_quarter"
    assert selection.derived is False
    assert selection.obs["end"] == "2025-12-27"          # the last clean quarter
    assert selection.obs["val"] == 53925000000
    assert selection.period == "FY2026 Q1"
    assert selection.span_days == 90
    print("ok default span keeps the latest clean quarter (no behaviour change)")


def test_ytd_span_reconciles_to_a_quarter() -> None:
    selection = scf._select_metric_observation(
        _ocf_block(), "duration", "operating_cash_flow", cash_flow_span="ytd")
    assert selection is not None
    assert selection.conversion == "ytd_delta"
    assert selection.derived is True
    assert selection.obs["val"] == 105000000000 - 82000000000    # 9M - 6M = Q3
    assert selection.obs["end"] == "2026-06-27"                  # same filing as the income statement
    assert selection.period == "FY2026 Q3"
    assert selection.prior_end == "2026-03-28"
    assert selection.span_days == 91
    assert "operating_cash_flow(FY2026 9M)" in selection.formula   # names the spans differenced
    assert "operating_cash_flow(FY2026 6M)" in selection.formula
    print("ok ytd span differences cumulative rows into the latest quarter")


def test_ytd_without_prior_is_labeled_cumulative() -> None:
    block = _tagblock("PaymentsToAcquirePropertyPlantAndEquipment", [
        _row(None, "2025-09-28", "2026-06-27", 8500000000, fy=2026, fp="Q3",
             filed="2026-07-31"),
    ])
    selection = scf._select_metric_observation(
        block, "duration", "capex", cash_flow_span="ytd")
    assert selection is not None
    assert selection.conversion == "ytd_as_filed"
    assert selection.derived is False
    assert selection.obs["val"] == 8500000000
    assert selection.period == "FY2026 9M"      # never labeled as a quarter
    print("ok ytd without a prior cumulative row stays as filed, labeled 9M")


def test_period_label_never_calls_a_cumulative_value_a_quarter() -> None:
    assert scf._period_label({"fy": 2026, "fp": "Q3", "start": "2025-09-28",
                              "end": "2026-06-27"}) == "FY2026 9M"
    assert scf._period_label({"fy": 2026, "fp": "Q2", "start": "2025-09-28",
                              "end": "2026-03-28"}) == "FY2026 6M"
    assert scf._period_label({"fy": 2026, "fp": "Q3", "start": "2026-03-29",
                              "end": "2026-06-27"}) == "FY2026 Q3"
    assert scf._period_label({"fy": 2025, "fp": "FY", "start": "2024-09-29",
                              "end": "2025-09-27"}) == "FY2025"
    assert scf._period_label({"fy": 2026, "fp": "Q3", "end": "2026-06-27"}) == "FY2026 Q3"
    print("ok span-aware period labels: 6M/9M cumulative rows are never called quarters")


def test_fetch_ledgers_the_delta_and_keeps_latest_quarter() -> None:
    with mock.patch.object(scf, "load_facts",
                           return_value=("0000320193", _facts(), "https://data.sec.gov/x")):
        result = scf.fetch_company_financials("AAPL", as_of="2026-10-02",
                                              cash_flow_span="ytd")

    metrics = {record.metric: record for record in result.records}
    ocf = metrics["operating_cash_flow"]
    assert ocf.derived is True
    assert ocf.value == 23000000000
    assert ocf.period == "FY2026 Q3"
    assert ocf.posture.claim_type == "derived_calculation"      # promoted safety net
    assert ocf.posture.authority_level == "L6"
    assert ocf.upstream_sources == "sec_companyfacts_api:AAPL"
    assert ocf.provenance["conversion"] == "ytd_delta"
    assert ocf.provenance["prior_period_end"] == "2026-03-28"
    assert ocf.provenance["period_end"] == "2026-06-27"

    capex = metrics["capex"]
    # The fixture has 3M and 9M capex rows only: 9M - 3M is six months, so the
    # adapter must not pass it off as a quarter.
    assert capex.derived is True
    assert capex.value == 8500000000 - 2373000000
    assert capex.provenance["conversion"] == "ytd_delta_span"
    assert capex.period == "FY2026 6M"
    assert capex.provenance["prior_period_end"] == "2025-12-27"

    revenue = metrics["revenue"]
    assert revenue.derived is False
    assert revenue.period == "FY2026 Q3"
    assert revenue.provenance["conversion"] == "single_quarter"

    # The delta must reach the ledger, and its evidence row must carry the formula.
    # Scratch space must sit on the same drive as the repo: emit_bundle records
    # dataset_location with os.path.relpath, which cannot cross Windows drives.
    scratch = ROOT / "local"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as out:
        emit_bundle(result.records, out_dir=out, research_object="AAPL",
                    market_scope="US", endpoint="sec://companyfacts")
        with open(Path(out) / "calculation-ledger.csv", encoding="utf-8") as fh:
            ledger = {row["metric"]: row for row in csv.DictReader(fh)}
        with open(Path(out) / "evidence-log.csv", encoding="utf-8") as fh:
            evidence = {row["claim_area"]: row for row in csv.DictReader(fh)}

    assert "operating_cash_flow" in ledger
    assert ledger["operating_cash_flow"]["formula"].startswith("operating_cash_flow(")
    assert ledger["operating_cash_flow"]["input_sources"] == "sec_companyfacts_api:AAPL"
    assert "capex" in ledger                       # widened span is still Mira-derived
    assert "revenue" not in ledger                 # disclosed quarter needs no ledger
    assert "conversion=ytd_delta" in evidence["operating_cash_flow"]["notes"]
    assert "period_end=2026-06-27" in evidence["operating_cash_flow"]["notes"]
    print("ok ytd deltas are ledgered with honest span labels; revenue stays a clean quarter")


def test_prior_prefers_a_one_quarter_difference() -> None:
    """A cumulative pool without the 6M row must not fake a quarter."""
    block = _tagblock("NetCashProvidedByUsedInOperatingActivities", [
        _row(None, "2025-09-28", "2025-12-27", 53925000000, fy=2026, fp="Q1",
             filed="2026-01-30"),
        _row(None, "2025-09-28", "2026-03-28", 82000000000, fy=2026, fp="Q2",
             filed="2026-05-01"),
        _row(None, "2025-09-28", "2026-06-27", 105000000000, fy=2026, fp="Q3",
             filed="2026-07-31"),
    ])
    selection = scf._select_metric_observation(
        block, "duration", "operating_cash_flow", cash_flow_span="ytd")
    assert selection.prior_end == "2026-03-28"        # the 6M row, giving 9M - 6M
    assert selection.conversion == "ytd_delta"
    assert selection.span_days == 91

    wide = _tagblock("NetCashProvidedByUsedInOperatingActivities", [
        _row(None, "2025-09-28", "2025-12-27", 53925000000, fy=2026, fp="Q1",
             filed="2026-01-30"),
        _row(None, "2025-09-28", "2026-06-27", 105000000000, fy=2026, fp="Q3",
             filed="2026-07-31"),
    ])
    widened = scf._select_metric_observation(
        wide, "duration", "operating_cash_flow", cash_flow_span="ytd")
    assert widened.conversion == "ytd_delta_span"
    assert widened.period == "FY2026 6M"
    assert widened.span_days == 182
    print("ok the prior cumulative row is chosen to yield a quarter, else labeled by span")


def test_unknown_span_is_rejected() -> None:
    try:
        scf.fetch_company_financials("AAPL", cash_flow_span="monthly")
    except ValueError as exc:
        assert "cash_flow_span" in str(exc)
    else:
        raise AssertionError("expected ValueError for an unknown span policy")
    print("ok unknown span policies are rejected before any fetch")


def test_screening_row_exposes_period_bases() -> None:
    row = screening._row(
        "AAPL", "2026-10-02", "US", "pass", {"min_fcf_yield": 0.02},
        {"fcf_yield": 0.0363, "revenue_yoy": 0.1636},
        basis="flows FY2025; yoy FY2026 Q3 vs FY2025 Q3; price 2026-10-01",
        price_as_of="2026-10-01", gaps=[],
        cash_flow_period_end="2025-09-27", revenue_yoy_period="FY2026 Q3 vs FY2025 Q3",
    )
    assert row["cash_flow_period_end"] == "2025-09-27"
    assert row["revenue_yoy_period"] == "FY2026 Q3 vs FY2025 Q3"
    assert "yoy FY2026 Q3" in row["fundamentals_basis"]

    # The template header must cover every key the row builder emits, otherwise
    # emit_watchlist_rows raises ValueError at write time.
    columns = screening.template_columns()
    missing = sorted(set(row) - set(columns))
    assert not missing, f"screening template is missing row keys: {missing}"
    print("ok screening rows carry explicit cash-flow and revenue-YoY period bases")


def test_screen_one_marks_both_bases() -> None:
    with (
        mock.patch.object(screening.scf, "load_facts",
                          return_value=("0000320193", _facts(), "https://data.sec.gov/x")),
        mock.patch.object(screening, "_last_close", return_value=(200.0, "2026-10-01")),
    ):
        row, _records = screening._screen_one(
            "AAPL", "0000320193", {"min_fcf_yield": 0.0}, "2026-10-02", "US")

    # fcf_yield is annual (FY2025), revenue_yoy is quarterly (FY2026 Q3): both named.
    assert row["cash_flow_period_end"] == "2025-09-27"
    assert row["revenue_yoy_period"] == "FY2026 Q3 vs FY2025 Q3"
    assert "flows FY2025" in row["fundamentals_basis"]
    assert "yoy FY2026 Q3 vs FY2025 Q3" in row["fundamentals_basis"]
    assert row["screen_status"] == "pass"
    print("ok screen rows name the annual cash-flow basis and the quarterly YoY basis")


def main() -> int:
    test_quarter_span_keeps_existing_behaviour()
    test_ytd_span_reconciles_to_a_quarter()
    test_ytd_without_prior_is_labeled_cumulative()
    test_period_label_never_calls_a_cumulative_value_a_quarter()
    test_fetch_ledgers_the_delta_and_keeps_latest_quarter()
    test_prior_prefers_a_one_quarter_difference()
    test_unknown_span_is_rejected()
    test_screening_row_exposes_period_bases()
    test_screen_one_marks_both_bases()
    print("mira_data_sec_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
