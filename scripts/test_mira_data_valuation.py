#!/usr/bin/env python3
"""Regression tests for the stock-level valuation snapshot channel (L5).

Offline: the vendor CLI runner is patched, so nothing is executed. The tests pin the five ratio
fields, the L5 market-pricing posture, the 100-code cap, the "history is not available" note, and
the two honesty rules that matter here - a missing field is skipped rather than zeroed, and a
negative ratio is passed through rather than filtered, because the vendor's own boundary statement
allows both.
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
from tools.mira_data.adapters import hithink_valuation as hv
from tools.mira_data.emit import emit_bundle


ENVELOPE = {"item": [
    {"thscode": "600519.SH", "ticker": "600519", "name": "贵州茅台", "pe_ttm": 19.320898,
     "pe_mrq": 17.671698, "pb_mrq": 6.26211, "ps_ttm": 9.082149, "pcf_ttm": 13.211237},
    {"thscode": "000001.SZ", "ticker": "000001", "name": "平安银行", "pe_ttm": 5.166398,
     "pe_mrq": 4.368899, "pb_mrq": 0.479538, "ps_ttm": 1.692317, "pcf_ttm": None},
], "timestamp": 1791014378000, "total": 2}


def _fetch(envelope=None, symbol="600519,000001", **kwargs):
    payload = ENVELOPE if envelope is None else envelope
    with mock.patch.object(hv, "_run", return_value=payload) as runner:
        return hv.fetch_valuation_snapshot(symbol, as_of="2026-10-03", **kwargs), runner


def test_five_ratios_per_name_with_the_l5_posture() -> None:
    result, runner = _fetch()
    assert len(result.records) == 9, "five fields for the first name, four for the second"
    record = next(r for r in result.records if r.metric == "pe_ttm"
                  and r.research_object == "600519.SH")
    assert record.family == "valuation_snapshot" and record.unit == "ratio"
    assert record.value == 19.320898
    assert record.posture.source_id == "hithink_finance_api"
    assert record.posture.authority_level == "L5"
    assert record.posture.claim_type == "market_pricing"
    assert record.provenance["vendorDay"] == "2026-10-03"
    assert record.provenance["historyAvailable"] is False
    assert "no percentile" in record.provenance["historyNote"]
    arguments = runner.call_args[0][0]
    assert arguments[:2] == ["valuation", "snapshot"]
    assert arguments[arguments.index("--thscodes") + 1] == "600519.SH,000001.SZ"
    assert arguments[-2:] == ["--format", "json"]
    print("ok five ratios per name, L5 market pricing, and the CLI is called with suffixed codes")


def test_missing_and_negative_values_are_not_laundered() -> None:
    result, _runner = _fetch()
    assert not [r for r in result.records if r.research_object == "000001.SZ"
                and r.metric == "pcf_ttm"], "a null field must be skipped, never zeroed"

    negative = {"item": [{"thscode": "600519.SH", "name": "贵州茅台", "pe_ttm": -12.5,
                          "pb_mrq": 0.0}], "timestamp": 1791014378000, "total": 1}
    result, _r = _fetch(negative)
    records = result.records
    metrics = {r.metric: r.value for r in records}
    assert metrics["pe_ttm"] == -12.5, "a negative ratio is the vendor's answer, not an error"
    assert metrics["pb_mrq"] == 0.0, "a published zero stays a zero"
    assert len(records) == 2
    print("ok nulls are skipped, negatives and published zeros pass through")


def test_caps_dedupe_and_gaps() -> None:
    over = ",".join(f"{600000 + index}" for index in range(hv.MAX_CODES + 1))
    try:
        hv.fetch_valuation_snapshot(over)
    except net.FetchError as exc:
        assert "hithink_valuation_batch_too_large" in str(exc) and "100" in str(exc)
    else:
        raise AssertionError("the vendor cap must be enforced locally with a clear message")

    _result, runner = _fetch(symbol="600519,600519.SH, 000001")
    assert runner.call_args[0][0][runner.call_args[0][0].index("--thscodes") + 1] == \
        "600519.SH,000001.SZ", "duplicates collapse and the given order is kept"

    try:
        hv.fetch_valuation_snapshot("")
    except net.FetchError as exc:
        assert "no A-share thscode" in str(exc)
    else:
        raise AssertionError("an empty code list must be rejected before any CLI call")

    try:
        _fetch({"item": [], "timestamp": 1791014378000, "total": 0})
    except net.FetchError as exc:
        assert "hithink_valuation_gap" in str(exc)
    else:
        raise AssertionError("an empty vendor payload must be a labelled gap")

    try:
        _fetch({"item": [{"thscode": "600519.SH", "name": "贵州茅台"}],
                "timestamp": 1791014378000, "total": 1})
    except net.FetchError as exc:
        assert "no valuation field" in str(exc)
    else:
        raise AssertionError("rows without any ratio must not produce an empty success")
    print("ok the cap, de-duplication, order and both empty cases are enforced")


def test_emitted_rows_are_market_pricing() -> None:
    result, _runner = _fetch()
    scratch = ROOT / "local"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as out:
        emitted = emit_bundle(result.records, out_dir=out, research_object="600519.SH",
                              market_scope="CN",
                              endpoint=hv.ENDPOINT.format(symbol="600519.SH"))
        assert emitted["calculation_ledger"] is None
        with open(Path(out) / "evidence-log.csv", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    assert {row["claim_type"] for row in rows} == {"market_pricing"}
    assert {row["evidence_category"] for row in rows} == {"market_pricing"}
    assert {row["authority_level"] for row in rows} == {"L5"}
    assert {row["verification_status"] for row in rows} == {"verified"}
    assert all("historyAvailable=False" in row["notes"] for row in rows)
    print("ok rows emit as verified L5 market pricing that declares itself a snapshot")


def main() -> int:
    test_five_ratios_per_name_with_the_l5_posture()
    test_missing_and_negative_values_are_not_laundered()
    test_caps_dedupe_and_gaps()
    test_emitted_rows_are_market_pricing()
    print("mira_data_valuation_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
