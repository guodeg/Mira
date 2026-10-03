#!/usr/bin/env python3
"""Offline tests for the executive shareholding-change channel (L5 relay).

The rows come from an aggregator, so the tests pin both the parsing and the honesty labels: the
signed share count keeps its sign (a sale is negative), a missing holding is skipped rather than
zeroed, only rows inside the requested window are emitted, and every record states that the
authoritative filing lives at the exchange/CNINFO instead.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import net
from tools.mira_data.adapters import em_insider as em


PAYLOAD = {"success": True, "result": {"count": 172291, "data": [
    {"CHANGE_DATE": "2026-09-30 00:00:00", "DSE_PERSON_NAME": "张爱江", "CHANGE_SHARES": -555194,
     "AVERAGE_PRICE": 26.76, "CHANGE_AMOUNT": -14860000, "CHANGE_RATIO": -0.05,
     "BEGIN_HOLD_NUM": 34857012, "END_HOLD_NUM": 34301818, "CHANGE_REASON": "竞价交易",
     "HOLD_TYPE": "A股", "GGEID": "abc123", "SECURITY_CODE": "600519",
     "DERIVE_SECURITY_CODE": "600519.SH"},
    {"CHANGE_DATE": "2015-01-05 00:00:00", "DSE_PERSON_NAME": "旧记录", "CHANGE_SHARES": 700,
     "AVERAGE_PRICE": None, "CHANGE_AMOUNT": None, "BEGIN_HOLD_NUM": None, "END_HOLD_NUM": None,
     "CHANGE_REASON": "二级市场买卖", "HOLD_TYPE": "A股", "GGEID": "old1",
     "SECURITY_CODE": "600519", "DERIVE_SECURITY_CODE": "600519.SH"},
]}}


def _fetch(payload=None, **kwargs):
    data = PAYLOAD if payload is None else payload
    with mock.patch.object(em.net, "get_json", return_value=data) as getter:
        result = em.fetch_executive_holdings("600519", as_of="2026-10-03",
                                            since="2016-01-01", **kwargs)
    return result, getter


def test_signed_shares_and_the_metrics_set() -> None:
    result, getter = _fetch()
    metrics = {r.metric: r for r in result.records}
    assert set(metrics) == {"insider_change_shares", "insider_change_amount",
                            "insider_avg_price", "insider_hold_after"}
    shares = metrics["insider_change_shares"]
    assert shares.value == -555194.0, "a sale keeps its negative sign"
    assert shares.unit == "shares" and shares.family == "ownership_short_interest"
    assert shares.research_object == "600519.SH" and shares.period == "2026-09-30"
    assert shares.posture.authority_level == "L5"
    assert shares.posture.claim_type == "reported_metric"
    assert "减持" in shares.claim_text
    assert metrics["insider_hold_after"].value == 34301818.0
    assert metrics["insider_avg_price"].unit == "CNY_per_share"

    url = getter.call_args[0][0]
    assert "RPT_EXECUTIVE_HOLD_DETAILS" in url
    assert 'filter=%28SECURITY_CODE%3D%22600519%22%29' in url or 'SECURITY_CODE="600519"' in url, url
    assert "pageSize=20" in url
    print("ok signed shares, the four metrics and the bare-code filter are pinned")


def test_missing_and_out_of_window_rows_are_dropped_not_zeroed() -> None:
    result, _getter = _fetch()
    assert len(result.records) == 4, "the 2015 row falls outside the requested window"
    assert {r.period for r in result.records} == {"2026-09-30"}
    assert all(r.provenance["vendorId"] == "abc123" for r in result.records)
    record = result.records[0]
    assert record.provenance["changeSharesSigned"] == -555194.0
    assert record.provenance["direction"] == "减持"
    assert record.provenance["holdBefore"] == 34857012.0
    assert "never zeroed" in record.provenance["missingPolicy"]
    assert "CNINFO" in record.provenance["authorityNote"]

    only_missing = {"result": {"count": 1, "data": [
        {"CHANGE_DATE": "2026-09-30 00:00:00", "DSE_PERSON_NAME": "甲", "CHANGE_SHARES": None,
         "END_HOLD_NUM": 100, "GGEID": "x"}]}}
    try:
        em.fetch_executive_holdings("600519", as_of="2026-10-03", since="2016-01-01") if False \
            else _fetch(only_missing)
    except net.FetchError as exc:
        assert "eastmoney_source_gap" in str(exc) and "usable share count" in str(exc)
    else:
        raise AssertionError("a row without a share count must not become a claim")
    print("ok out-of-window and unusable rows are dropped, and emptiness is a labelled gap")


def test_empty_and_event_caps() -> None:
    for empty in ({"result": {"count": 0, "data": []}}, {"result": {}}):
        with mock.patch.object(em.net, "get_json", return_value=empty):
            try:
                em.fetch_executive_holdings("600519", as_of="2026-10-03", since="2016-01-01")
            except net.FetchError as exc:
                assert "eastmoney_source_gap" in str(exc) and "600519.SH" in str(exc)
            else:
                raise AssertionError("an empty vendor answer must be a labelled gap")

    result, _getter = _fetch(max_items=1)
    assert len({r.provenance["vendorId"] for r in result.records}) == 1
    assert result.records[0].provenance["batchSize"] == 1
    print("ok empty answers are gaps and the event cap is passed to the vendor")


def main() -> int:
    test_signed_shares_and_the_metrics_set()
    test_missing_and_out_of_window_rows_are_dropped_not_zeroed()
    test_empty_and_event_caps()
    print("mira_data_insider_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
