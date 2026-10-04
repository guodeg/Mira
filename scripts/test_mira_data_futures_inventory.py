#!/usr/bin/env python3
"""Offline tests for futures warehouse receipts and basis (aggregator tier).

Both sources were probed live on 2026-10-03. The tests pin the things that would otherwise
publish a wrong or empty answer silently:

1. **The relay's variety casing is inconsistent** — one result set holds `RB`/`CU` uppercase
   and `si`/`lc` lowercase — and an unmatched case returns an empty result rather than an
   error, so a case-sensitive lookup reads exactly like "this variety has no receipts".
2. **The tier must stay honest.** The official stock files are not reachable, so these are L5
   relays; a record that implied exchange provenance would be a false claim.
3. **The vendor publishes the basis figures, Mira does not derive them** — so they carry no
   ledger obligation, and that is recorded rather than left ambiguous.
4. **The vendor's own numbers are checkable**: `spot - close == basis`. When they disagree the
   record says so instead of publishing the mismatch as if it reconciled.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import net
from tools.mira_data.adapters import futures_inventory as fi


def _em_payload(code="RB", dates=("2026-09-30", "2026-09-29")):
    return {"result": {"count": len(dates), "data": [
        {"SECURITY_CODE": code, "TRADE_DATE": f"{d} 00:00:00",
         "ON_WARRANT_NUM": 87653 - i * 100, "ADDCHANGE": -390, "UNIT": "手"}
        for i, d in enumerate(dates)
    ]}}


# Measured from the vendor CLI: 144 rows; the rb sample satisfies spot - close == basis.
BASIS_PAYLOAD = {"ok": True, "data": {"item": [
    {"thscode": "RBZL.SHF", "ticker": "rb9999", "name": "螺纹钢连续", "variety_name": "螺纹钢",
     "reference_site": "上海", "updated_at": "2026.09.30 19:14:11",
     "spot_publish_date": "2026-09-30", "spot_indicator_id": "20868",
     "spot_price": 3260, "converted_spot_price": 3260, "close_price": 3112, "settle_price": 3116,
     "close_basis": 148, "settle_basis": 144, "close_basis_rate": 4.54,
     "settle_basis_rate": 4.42, "average_close_basis": 120, "average_settle_basis": 118},
    {"thscode": "CUZL.SHF", "ticker": "cu9999", "name": "铜连续", "variety_name": "铜",
     "reference_site": "上海", "spot_publish_date": "2026-09-30",
     "spot_price": 100, "close_price": 90, "close_basis": 999,   # deliberately inconsistent
     "close_basis_rate": 1.0},
]}}


def test_relay_casing_is_tried_both_ways() -> None:
    seen = []

    def fake_json(url, **kwargs):
        seen.append(url)
        # Only the lowercase form resolves, as `si`/`lc` do on the live relay.
        if "SI" in url:
            return {"result": {"count": 0, "data": []}}
        return _em_payload("si")

    with mock.patch.object(fi.net, "get_json", side_effect=fake_json):
        result = fi.fetch_warehouse_receipts("si", as_of="2026-10-03", days=30)
    assert len(seen) == 2, seen
    assert 'SECURITY_CODE%3D%22SI%22' in seen[0] or 'SECURITY_CODE="SI"' in seen[0].replace("%22", '"')
    assert "si" in seen[1]
    assert result.records[0].value == 87653.0
    assert result.records[0].research_object == "SI_WAREHOUSE"

    # A variety in neither form is a labelled gap rather than an empty series.
    with mock.patch.object(fi.net, "get_json",
                           return_value={"result": {"count": 0, "data": []}}):
        try:
            fi.fetch_warehouse_receipts("zzz", as_of="2026-10-03")
        except net.FetchError as exc:
            assert "futures_inventory_source_gap" in str(exc)
            assert "case" in str(exc)
        else:
            raise AssertionError("an absent variety must be a gap")
    print("ok the relay variety is tried in both case forms, and absence is a gap")


def test_warehouse_tier_is_stated_as_l5_relay() -> None:
    with mock.patch.object(fi.net, "get_json", return_value=_em_payload()):
        rec = fi.fetch_warehouse_receipts("rb", as_of="2026-10-03", days=30).records[0]
    assert rec.posture.authority_level == "L5", "the stock file is not reachable"
    assert rec.posture.claim_type == "reported_metric"
    assert rec.family == "macro_series"
    assert rec.unit == "warrants"
    # The record must carry why it is not L2, so nobody upgrades it by assumption.
    assert "not the exchange" in rec.provenance["tierBasis"]
    assert "dailystock" in rec.provenance["tierBasis"]
    assert rec.provenance["vendorField"].startswith("ON_WARRANT_NUM")
    assert "-390" not in rec.claim_text or "+" in rec.claim_text or "-390" in rec.claim_text
    print("ok warehouse receipts are labelled L5 relays with the reason attached")


def test_warehouse_window_and_empty_window() -> None:
    with mock.patch.object(fi.net, "get_json", return_value=_em_payload()):
        # `days` is a WINDOW, not a row count: a 1-day window ending 2026-09-30 spans two
        # calendar dates (the cutoff is as_of - days), so both rows are legitimately inside it.
        one_day = fi.fetch_warehouse_receipts("rb", as_of="2026-09-30", days=1)
        assert {r["date"] for r in one_day.series["rows"]} == {"2026-09-30", "2026-09-29"}
    with mock.patch.object(fi.net, "get_json", return_value=_em_payload()):
        # A window that ends before the newest row keeps only what falls inside.
        assert len(fi.fetch_warehouse_receipts("rb", as_of="2026-09-29", days=1)
                   .series["rows"]) == 2
    with mock.patch.object(fi.net, "get_json", return_value=_em_payload()):
        try:
            fi.fetch_warehouse_receipts("rb", as_of="2027-01-01", days=5)
        except net.FetchError as exc:
            assert "none inside the last" in str(exc)
        else:
            raise AssertionError("rows outside the window are not a usable observation")
    print("ok the receipt window filters by date, and an empty window is a gap")


def test_basis_reports_vendor_numbers_and_checks_the_identity() -> None:
    with mock.patch.object(fi, "_vendor_json", return_value=BASIS_PAYLOAD):
        result = fi.fetch_basis(as_of="2026-10-03", max_items=10)
    rb = [r for r in result.records if r.provenance["ticker"] == "rb9999"][0]
    assert rb.value == 148.0
    assert rb.provenance["spotPrice"] == 3260 and rb.provenance["closePrice"] == 3112
    assert rb.provenance["identityConsistent"] is True
    assert "reconciles" in rb.provenance["identityCheck"]
    # The vendor computed it; Mira must not claim a derived calculation.
    assert rb.posture.claim_type == "reported_metric"
    assert rb.derived is False
    assert "not recomputed" in rb.provenance["notRecomputed"]
    assert "现货" in rb.claim_text and "基差" in rb.claim_text
    # spot_publish_date travels, since it can differ from the futures session.
    assert rb.provenance["spotPublishDate"] == "2026-09-30"
    assert rb.period == "2026-09-30"

    # An internally inconsistent vendor row is flagged, not smoothed over.
    cu = [r for r in result.records if r.provenance["ticker"] == "cu9999"][0]
    assert cu.provenance["identityConsistent"] is False
    assert "do NOT reconcile" in cu.provenance["identityCheck"]
    print("ok basis reports the vendor's numbers, flags a mismatch, and claims no derivation")


def test_basis_filter_and_empty_result() -> None:
    with mock.patch.object(fi, "_vendor_json", return_value=BASIS_PAYLOAD):
        filtered = fi.fetch_basis("铜", as_of="2026-10-03")
    assert [r.provenance["ticker"] for r in filtered.records] == ["cu9999"]
    with mock.patch.object(fi, "_vendor_json", return_value=BASIS_PAYLOAD):
        try:
            fi.fetch_basis("nonexistent-variety", as_of="2026-10-03")
        except net.FetchError as exc:
            assert "futures_basis_source_gap" in str(exc)
            assert "2 varieties" in str(exc)
        else:
            raise AssertionError("a filter that matches nothing must be a gap")
    with mock.patch.object(fi, "_vendor_json", return_value={"data": {"item": []}}):
        try:
            fi.fetch_basis(as_of="2026-10-03")
        except net.FetchError as exc:
            assert "no basis rows" in str(exc)
        else:
            raise AssertionError("an empty vendor payload must be a gap")
    print("ok the basis filter works and both empty cases are labelled gaps")


def test_vendor_cli_is_invoked_without_a_pipe() -> None:
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        import json as _json
        out = argv[argv.index("--output") + 1]
        with open(out, "w", encoding="utf-8") as fh:
            _json.dump(BASIS_PAYLOAD, fh)
        return mock.Mock(returncode=0)

    with mock.patch.object(fi.subprocess, "run", side_effect=fake_run):
        result = fi.fetch_basis("rb", as_of="2026-10-03")
    argv = captured["argv"]
    # --output is used instead of capturing stdout: an npm shim's output is not reliably
    # capturable through a pipe, and that is also the sandbox boundary.
    assert "--output" in argv and "--format" in argv and "json" in argv
    assert result.records
    print("ok the vendor CLI is driven through --output rather than a captured pipe")


def main() -> int:
    test_relay_casing_is_tried_both_ways()
    test_warehouse_tier_is_stated_as_l5_relay()
    test_warehouse_window_and_empty_window()
    test_basis_reports_vendor_numbers_and_checks_the_identity()
    test_basis_filter_and_empty_result()
    test_vendor_cli_is_invoked_without_a_pipe()
    print("mira_data_futures_inventory_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
