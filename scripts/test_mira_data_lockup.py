#!/usr/bin/env python3
"""Offline tests for the lock-up expiry channel (L5 relay).

The live probe found that the relay's share quantity, ratio and market value do not reconcile
with each other, so the tests pin the consequence: only the date, the share type and the holder
count become claims, while the three ambiguous values travel as raw vendor data with an explicit
unit caveat. A test that let a share quantity through here would be the bug.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import net
from tools.mira_data.adapters import em_lockup as lk


PAYLOAD = {"result": {"count": 15, "data": [
    {"FREE_DATE": "2024-09-24 00:00:00", "FREE_SHARES_TYPE": "股权激励限售股份",
     "BATCH_HOLDER_NUM": 1, "FREE_SHARES": 390146.5544, "FREE_RATIO": 0.000822,
     "ALIFT_MARKET_CAP": 63369.73, "SECURITY_CODE": "300750", "ABLE_FREE_SHARES": 1,
     "CURRENT_FREE_SHARES": 2, "A20_ADJCHRATE": 0.5},
    {"FREE_DATE": "2023-09-25 00:00:00", "FREE_SHARES_TYPE": "首发原股东限售股份",
     "BATCH_HOLDER_NUM": 3, "FREE_SHARES": 389303.5353, "FREE_RATIO": 0.000853576117,
     "ALIFT_MARKET_CAP": 69543.785856, "SECURITY_CODE": "300750"},
]}}


def _fetch(payload=None, **kwargs):
    data = PAYLOAD if payload is None else payload
    with mock.patch.object(lk.net, "get_json", return_value=data) as getter:
        result = lk.fetch_lockup_schedule("300750", as_of="2026-10-03", **kwargs)
    return result, getter


def test_only_self_evident_fields_are_claimed() -> None:
    result, getter = _fetch()
    assert len(result.records) == 2
    record = result.records[0]
    assert record.family == "ownership_short_interest"
    assert record.metric == "lockup_expiry_holders" and record.value == 1.0
    assert record.unit == "holders" and record.period == "2024-09-24"
    assert record.research_object == "300750.SZ"
    assert record.posture.authority_level == "L5"
    assert "股权激励限售股份" in record.claim_text and "解禁" in record.claim_text
    for forbidden in ("share", "shares"):
        assert forbidden not in record.metric, "no share quantity may be claimed"
    raw = record.provenance["vendorRawUnverified"]
    assert raw["FREE_SHARES"] == 390146.5544 and raw["FREE_RATIO"] == 0.000822
    assert "unverified" in record.provenance["unitBasis"]
    assert "not" in record.provenance["claimScope"] or "are not" in record.provenance["claimScope"]
    assert "CNINFO" in record.provenance["upgradePath"]

    url = getter.call_args[0][0]
    assert "RPT_LIFT_STAGE" in url and "SECURITY_CODE" in url.replace("%22", '"')
    assert "sortColumns=FREE_DATE" in url
    print("ok the date, type and holder count are claimed; quantities and values are not")


def test_windows_gaps_and_holderless_rows() -> None:
    _result, _getter = _fetch(since="2024-01-01", until="2024-12-31")
    filtered, _g = _fetch(since="2024-01-01", until="2024-12-31")
    assert {r.period for r in filtered.records} == {"2024-09-24"}

    no_holders = {"result": {"count": 1, "data": [
        {"FREE_DATE": "2027-01-05 00:00:00", "FREE_SHARES_TYPE": "",
         "BATCH_HOLDER_NUM": None, "SECURITY_CODE": "300750"}]}}
    result, _g = _fetch(no_holders)
    record = result.records[0]
    assert record.value == 0.0 and record.provenance["holders"] is None
    assert "未标注股份类型" in record.claim_text, "a missing type is labelled, not invented"

    for empty in ({"result": {"count": 0, "data": []}}, {"result": {}}):
        with mock.patch.object(lk.net, "get_json", return_value=empty):
            try:
                lk.fetch_lockup_schedule("300750", as_of="2026-10-03")
            except net.FetchError as exc:
                assert "eastmoney_source_gap" in str(exc) and "300750.SZ" in str(exc)
            else:
                raise AssertionError("an empty vendor answer must be a labelled gap")

    undateable = {"result": {"count": 1, "data": [{"FREE_DATE": None, "SECURITY_CODE": "300750"}]}}
    with mock.patch.object(lk.net, "get_json", return_value=undateable):
        try:
            lk.fetch_lockup_schedule("300750", as_of="2026-10-03")
        except net.FetchError as exc:
            assert "usable date" in str(exc)
        else:
            raise AssertionError("a row without a date cannot be a schedule entry")
    print("ok windows filter, missing types are labelled, and both empty cases are gaps")


def main() -> int:
    test_only_self_evident_fields_are_claimed()
    test_windows_gaps_and_holderless_rows()
    print("mira_data_lockup_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
