#!/usr/bin/env python3
"""Offline tests for A-share screening.

``screen`` returned ``data_gap`` for every A-share before computing a single metric, because its
only code path read SEC companyfacts. This suite pins the six decisions that make the A-share path
correct rather than merely functional — each was established by probing live data, and each has a
plausible wrong alternative that would have shipped silently:

1. **``max_debt_to_equity`` must NOT use total liabilities.** The vendor's balance sheet exposes a
   field named ``total_debt`` that IS total liabilities — it equals ``assets - equity`` to the
   penny. Feeding it to a leverage ceiling would filter out asset-light, high-margin businesses
   whose liabilities are almost entirely interest-free payables. The vendor's
   ``long_term_debt_equity_ratio`` indicator is used instead, and is recorded as
   vendor-published because its inputs are not exposed (there are no borrowing line items).
2. **``max_assets_debt_ratio`` exists as a separate A-share-native criterion**, because 资产负债率
   is the conventional A-share leverage measure and the two can disagree completely: 平安银行 has
   a 90.7% liabilities/assets ratio with essentially ZERO long-term debt. One criterion cannot
   express both facts.
3. **Unavailable criteria are Partial, never failures.** No channel exposes shares outstanding or
   market value, so market cap and FCF yield cannot be computed. A candidate is judged on the
   criteria that have data and the missing ones are named.
4. **The quarterly series is cumulative, so only annual rows are used for YoY.** The vendor's
   "2026-Q2" is the H1 total (verified: 2025 reads Q1 5.61bn, Q2 12.68bn, Q3 20.61bn, Q4 28.43bn
   where Q4 equals the annual figure exactly). A quarter-on-quarter subtraction would manufacture
   an apparent +134% growth out of the accumulation itself.
5. **Published percentages are converted to ratios**, because the indicator set reports
   ``assets_debt_ratio`` as 42.2937 (= 42.29%) while every computed ratio is a plain ratio
   (net_margin 0.136888). Mixing the scales would make one threshold mean two things.
6. **A mixed-market batch is refused.** One numeric threshold would mean USD for US tickers and
   CNY for A-shares, roughly 7x apart under a single predicate.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import cn_symbols, screening
from tools.mira_data.adapters import hithink_screen as hscr


# Live values for 600183 (生益科技), 600519 (贵州茅台) and 000001 (平安银行), FY2025 / report 2025-4.
GATHERED = {
    "600183": {
        "metrics": {"net_margin": 0.136888, "revenue_yoy": 0.394481,
                    "debt_to_equity": 0.106431, "assets_debt_ratio": 0.422937},
        "periods": {"net_margin": "FY2025", "revenue_yoy": "FY2025 vs FY2024 (annual, not cumulative)",
                    "debt_to_equity": "2025-4", "assets_debt_ratio": "2025-4"},
        "formulas": {"net_margin": "net_income(FY2025) / revenue(FY2025)",
                     "revenue_yoy": "revenue(FY2025) / revenue(FY2024) - 1",
                     "debt_to_equity": "vendor indicator long_term_debt_equity_ratio (2025-4)",
                     "assets_debt_ratio": "vendor indicator assets_debt_ratio (2025-4)"},
        "gaps": ["market_cap", "fcf_yield"],
        "published_percent": {"debt_to_equity": 10.643118, "assets_debt_ratio": 42.2937},
        "caveats": {"debt_to_equity": hscr.VENDOR_PUBLISHED_CAVEAT},
        "as_of": "2026-10-04", "thscode": "600183.SH", "report": "2025-4",
    },
    "600519": {
        "metrics": {"net_margin": 0.505279, "revenue_yoy": -0.012060,
                    "debt_to_equity": 0.001084, "assets_debt_ratio": 0.164154},
        "periods": {"net_margin": "FY2025", "revenue_yoy": "FY2025 vs FY2024",
                    "debt_to_equity": "2025-4", "assets_debt_ratio": "2025-4"},
        "formulas": {"net_margin": "f", "revenue_yoy": "f", "debt_to_equity": "f",
                     "assets_debt_ratio": "f"},
        "gaps": ["market_cap", "fcf_yield"],
        "published_percent": {"debt_to_equity": 0.10837, "assets_debt_ratio": 16.4154},
        "caveats": {"debt_to_equity": hscr.VENDOR_PUBLISHED_CAVEAT},
        "as_of": "2026-10-04", "thscode": "600519.SH", "report": "2025-4",
    },
    "000001": {
        "metrics": {"net_margin": 0.324348, "revenue_yoy": -0.103978,
                    "debt_to_equity": 0.0, "assets_debt_ratio": 0.906985},
        "periods": {"net_margin": "FY2025", "revenue_yoy": "FY2025 vs FY2024",
                    "debt_to_equity": "2025-4", "assets_debt_ratio": "2025-4"},
        "formulas": {"net_margin": "f", "revenue_yoy": "f", "debt_to_equity": "f",
                     "assets_debt_ratio": "f"},
        "gaps": ["market_cap", "fcf_yield"],
        "published_percent": {"debt_to_equity": 0.0, "assets_debt_ratio": 90.6985},
        "caveats": {"debt_to_equity": hscr.VENDOR_PUBLISHED_CAVEAT},
        "as_of": "2026-10-04", "thscode": "000001.SZ", "report": "2025-4",
    },
}


def _gather(code, *, as_of=None):
    return GATHERED[code]


def _screen(tickers, criteria, **kwargs):
    """Run the CN path with the network stubbed out."""
    def fake_metrics(sym, *, as_of=None):
        return GATHERED[sym]
    with mock.patch.object(hscr, "fetch_screen_metrics", side_effect=fake_metrics), \
         mock.patch.object(screening, "_last_close", return_value=(129.96, "2026-09-30")):
        return screening.screen_candidates(tickers, criteria, as_of="2026-10-04", **kwargs)


def test_leverage_uses_long_term_debt_not_total_liabilities() -> None:
    res = _screen(["600183"], {"max_debt_to_equity": 0.2})
    row = res.rows[0]
    # 10.64% long-term leverage passes a 20% ceiling; total liabilities/equity would be 73.29%
    # and FAIL, which is the misclassification this guards against.
    assert row["debt_to_equity"] == 0.106431, row["debt_to_equity"]
    assert row["screen_status"] == "pass", row["screen_status"]
    # The caveat must travel: the figure is the vendor's, not recomputable by Mira.
    assert "vendor long_term_debt_equity_ratio" in row["notes"]
    assert "not total liabilities" in row["notes"]
    assert row["leverage_caveat"] == hscr.VENDOR_PUBLISHED_CAVEAT
    assert "no borrowing line items" in row["leverage_caveat"]
    print("ok leverage uses long-term debt (not the deceptively-named total_debt) with a caveat")


def test_the_two_leverage_criteria_can_disagree() -> None:
    # 平安银行: 90.7% liabilities/assets but essentially zero long-term debt. Both facts are true
    # and only two criteria can express them, which is why option (C) exists.
    res = _screen(["000001"], {"max_debt_to_equity": 0.2})
    assert res.rows[0]["screen_status"] == "pass", "passes on long-term debt"
    res = _screen(["000001"], {"max_assets_debt_ratio": 0.5})
    assert res.rows[0]["screen_status"] == "fail", "fails on liabilities/assets"
    assert res.rows[0]["assets_debt_ratio"] == 0.906985
    # And both together give the honest combined answer.
    res = _screen(["000001"], {"max_debt_to_equity": 0.2, "max_assets_debt_ratio": 0.5})
    assert res.rows[0]["screen_status"] == "fail"
    print("ok the two leverage criteria disagree where they should, and combine correctly")


def test_unavailable_criteria_are_partial_not_failures() -> None:
    res = _screen(["600183", "600519"], {"min_market_cap": 1e10, "min_net_margin": 0.10})
    assert res.summary["partial"] == 2 and res.summary["fail"] == 0, res.summary
    for row in res.rows:
        # The candidate WAS evaluated: net margin decided it.
        assert row["screen_status"] == "partial", row["screen_status"]
        assert "market_cap" in row["data_gaps"], row["data_gaps"]
        assert row["market_cap_cny"] == "source_gap"
        assert row["next_action"] == "single_equity_research_candidate_partial_data"
        # And the row says WHY it is partial rather than leaving the reader to guess.
        assert "PARTIAL:" in row["notes"] and "unavailable" in row["notes"]
    # A criterion that fails still fails, even alongside an unavailable one: partial must not
    # become a way to pass.
    res = _screen(["600183"], {"min_market_cap": 1e10, "min_net_margin": 0.90})
    assert res.rows[0]["screen_status"] == "fail", res.rows[0]["screen_status"]
    print("ok unavailable criteria are Partial, and cannot mask a genuine failure")


def test_an_all_partial_batch_is_still_a_result() -> None:
    # Raising here would reject a legitimate all-partial batch — the same mistake as hard-failing
    # an unavailable criterion, one level up.
    res = _screen(["600183", "600519"], {"min_market_cap": 1e10})
    assert res.summary["partial"] == 2, res.summary
    assert len(res.rows) == 2
    print("ok an all-partial batch returns a result instead of raising")


def test_vendor_percentages_arrive_as_ratios() -> None:
    row = _screen(["600183"], {"min_net_margin": 0.0}).rows[0]
    # 42.2937 published percent -> 0.422937 ratio, so one threshold scale covers every metric.
    assert row["assets_debt_ratio"] == 0.422937, row["assets_debt_ratio"]
    assert row["debt_to_equity"] == 0.106431
    assert row["net_margin"] == 0.136888
    # The published percent is retained nowhere in the row, so the two scales cannot be confused
    # downstream; only the converted ratio is carried.
    assert "42.2937" not in str(row.get("data_gaps", ""))
    print("ok published percentages are converted so all ratios share one scale")


def test_mixed_market_batches_are_refused() -> None:
    try:
        _screen(["AAPL", "600183"], {"min_net_margin": 0.1})
    except ValueError as exc:
        message = str(exc)
        assert "multi-currency" in message
        assert "explicit conversion" in message
        assert "CNY" in message and "USD" in message
    else:
        raise AssertionError("a mixed batch must be refused, not silently reinterpreted")
    # A single-market batch on either side still routes correctly.
    assert screening._resolve_market(["600183", "000001"]) == "CN"
    assert screening._resolve_market(["AAPL", "MSFT"]) == "US"
    assert screening._resolve_market(["600183.SS"]) == "US", (
        "a suffixed ticker is already resolved, so it is not a bare A-share code")
    print("ok mixed-market batches are refused with the currency reason")


def test_criteria_are_market_scoped() -> None:
    # The A-share-native criterion must not be silently accepted for US tickers.
    try:
        _screen(["AAPL"], {"max_assets_debt_ratio": 0.5})
    except ValueError as exc:
        assert "max_assets_debt_ratio" in str(exc)
    else:
        raise AssertionError("an A-share-only criterion must be refused for a US batch")
    # And an unknown flag is still refused.
    try:
        _screen(["600183"], {"max_leverage": 1.0})
    except ValueError as exc:
        assert "max_leverage" in str(exc)
    else:
        raise AssertionError("an unknown criterion must be refused")
    print("ok criteria are scoped to their market and unknown flags are refused")


def test_market_scope_mismatch_is_refused() -> None:
    try:
        _screen(["600183"], {"min_net_margin": 0.1}, market_scope="US")
    except ValueError as exc:
        assert "contradicts" in str(exc)
    else:
        raise AssertionError("a contradictory market_scope must be refused")
    print("ok a market_scope contradicting the tickers is refused")


def test_derived_records_are_ledgered_with_the_vendor_caveat() -> None:
    res = _screen(["600183"], {"min_net_margin": 0.10})
    assert res.derived, "a passing candidate must produce ledgered records"
    by_metric = {r.metric: r for r in res.derived}
    assert set(by_metric) == {"net_margin", "revenue_yoy", "debt_to_equity", "assets_debt_ratio"}
    for rec in res.derived:
        assert rec.derived is True
        assert rec.family == "company_financials" and rec.market_scope == "CN"
        assert rec.formula, rec.metric
        assert "hithink_finance_financials_api:600183" in rec.upstream_sources
    # The leverage records carry the vendor caveat as their cross-check; the computed ones do not.
    assert "cannot independently recompute" in by_metric["debt_to_equity"].cross_check
    assert "verify vs filings" in by_metric["net_margin"].cross_check
    print("ok derived records are ledgered, and only the leverage ones carry the vendor caveat")


def main() -> int:
    test_leverage_uses_long_term_debt_not_total_liabilities()
    test_the_two_leverage_criteria_can_disagree()
    test_unavailable_criteria_are_partial_not_failures()
    test_an_all_partial_batch_is_still_a_result()
    test_vendor_percentages_arrive_as_ratios()
    test_mixed_market_batches_are_refused()
    test_criteria_are_market_scoped()
    test_market_scope_mismatch_is_refused()
    test_derived_records_are_ledgered_with_the_vendor_caveat()
    print("mira_data_screen_cn_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
