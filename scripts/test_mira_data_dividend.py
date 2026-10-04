#!/usr/bin/env python3
"""Offline tests for the A-share 分红送配 (dividend) adapter.

The fixture is the live 华泰证券 row for 2026-10-23, where the vendor's own numbers establish
three conventions that a casual read gets wrong:

1. **Three ratio fields, only one a yield.** ``DIVIDENT_RATIO`` 0.010112 is the dividend yield as
   a **ratio** (= 1.01%). ``BONUS_IT_RATIO`` is 送转 shares per 10 shares and ``IT_RATIO`` is
   转增 per 10 shares — and both are **null** here because only cash was paid. Reading them as
   yields would invent a share distribution that was never declared.
2. **``PRETAX_BONUS_RMB`` is per share while the plan text is per 10.** "10派1.80元" beside a
   field of ``1.8``. Using the plan text's number as per-share overstates the dividend tenfold.
3. **``EX_DIVIDEND_DAYS`` is a signed offset, not a date** — it read ``-18`` because the ex-date
   was still 18 days out at read time.

A fourth item is a formatting trap rather than a data one: a per-share cash amount rendered at
zero decimals turns 0.35 into "0" and 1.8 into "2", which is a wrong number rather than a rounded
one.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import net
from tools.mira_data.adapters import em_dividend as dv


HUATAI = {
    "SECUCODE": "601688.SH", "SECURITY_CODE": "601688", "SECURITY_NAME_ABBR": "华泰证券",
    "BONUS_IT_RATIO": None, "BONUS_RATIO": None, "IT_RATIO": None,
    "PRETAX_BONUS_RMB": 1.8, "PLAN_NOTICE_DATE": "2026-06-04 00:00:00",
    "EQUITY_RECORD_DATE": "2026-10-22 00:00:00", "EX_DIVIDEND_DATE": "2026-10-23 00:00:00",
    "REPORT_DATE": "2026-06-30 00:00:00", "ASSIGN_PROGRESS": "董事会决议通过",
    "IMPL_PLAN_PROFILE": "10派1.80元(含税)", "NOTICE_DATE": "2026-08-29 00:00:00",
    "EX_DIVIDEND_DAYS": -18, "BASIC_EPS": 1.25, "BVPS": 19.812506382237,
    "PER_CAPITAL_RESERVE": 7.625658614054, "PER_UNASSIGN_PROFIT": 7.018253050481,
    "PNP_YOY_RATIO": 54.866156523004, "TOTAL_SHARES": 9026863786,
    "PUBLISH_DATE": "2026-08-29 00:00:00", "DIVIDENT_RATIO": 0.010112359551,
    "D10_CLOSE_ADJCHRATE": 2.30245945, "BD10_CLOSE_ADJCHRATE": -4.24959656,
}

# A cash-plus-share plan, where the two non-yield ratios are actually populated.
CASH_AND_SHARES = dict(
    HUATAI, SECURITY_CODE="000001", SECURITY_NAME_ABBR="平安银行",
    IMPL_PLAN_PROFILE="10派2.00元(含税)转增5股", PRETAX_BONUS_RMB=0.35,
    DIVIDENT_RATIO=0.0025, BONUS_IT_RATIO=5.0, IT_RATIO=3.0,
    EX_DIVIDEND_DATE="2026-06-20 00:00:00", EX_DIVIDEND_DAYS=110,
)


def _fetch(rows, **kwargs):
    payload = {"result": {"count": len(rows), "data": rows}}
    with mock.patch.object(dv.net, "get_json", return_value=payload):
        return dv.fetch_dividends("601688", as_of="2026-10-04", **kwargs)


def test_dividend_ratio_is_a_ratio_and_becomes_a_percent() -> None:
    rec = _fetch([HUATAI]).records[0]
    assert rec.provenance["dividendRatioAsReturned"] == 0.010112359551
    # 0.0101 is 1.01%, not 0.0101%.
    assert abs(rec.provenance["dividendYieldPercent"] - 1.0112359551) < 1e-6
    assert "1.0112%" in rec.claim_text, rec.claim_text
    assert "RATIO" in rec.provenance["ratioUnitNote"]
    print("ok DIVIDENT_RATIO is converted from ratio to percent")


def test_non_yield_ratios_stay_null_rather_than_becoming_zero() -> None:
    rec = _fetch([HUATAI]).records[0]
    # Cash-only plans carry nulls here; a zero would claim "declared 0 bonus shares".
    assert rec.provenance["bonusSharesPer10"] is None
    assert rec.provenance["transferSharesPer10"] is None
    # When a plan does include shares, they are carried as per-10 counts, not percentages.
    both = _fetch([CASH_AND_SHARES]).records[0]
    assert both.provenance["bonusSharesPer10"] == 5.0
    assert both.provenance["transferSharesPer10"] == 3.0
    print("ok 送转/转增 stay null when absent and are per-10 counts when present")


def test_per_share_amount_is_not_the_per_ten_number() -> None:
    rec = _fetch([HUATAI]).records[0]
    assert rec.provenance["pretaxBonusPerShareCNY"] == 1.8, "the plan text says 10派1.80元"
    assert rec.value == 1.8 and rec.unit == "CNY_per_share"
    # Both travel, so the per-share field can be audited against the per-10 plan text.
    assert rec.provenance["planProfile"] == "10派1.80元(含税)"
    assert "PER SHARE" in rec.provenance["perShareNote"]
    print("ok the per-share field is used and the per-10 plan text travels alongside")


def test_per_share_amount_survives_formatting() -> None:
    # 0.35 at zero decimals is "0"; 1.8 is "2". Both are wrong numbers, not rounded ones.
    rec = _fetch([CASH_AND_SHARES]).records[0]
    assert "0.35" in rec.claim_text, rec.claim_text
    assert rec.provenance["pretaxBonusPerShareCNY"] == 0.35
    assert "2.00%" in rec.claim_text or "0.2500%" in rec.claim_text, rec.claim_text
    assert dv._fmt(None) == "n/a", "an absent value must not render as 0"
    assert dv._fmt(0.35, 2) == "0.35"
    assert dv._fmt(1.8, 2) == "1.80"
    print("ok a sub-yuan per-share amount renders instead of rounding to zero")


def test_ex_dividend_days_is_a_signed_offset() -> None:
    rec = _fetch([HUATAI]).records[0]
    # Negative means the ex-date had not arrived at read time.
    assert rec.provenance["exDividendDays"] == -18
    assert rec.period == "2026-10-23", "the claim is anchored on the ex-date"
    assert "negative" in rec.provenance["exDividendDaysNote"]
    past = _fetch([CASH_AND_SHARES]).records[0]
    assert past.provenance["exDividendDays"] == 110
    print("ok EX_DIVIDEND_DAYS is carried as a signed offset and the ex-date anchors the claim")


def test_forward_returns_are_reported_not_computed() -> None:
    rec = _fetch([HUATAI]).records[0]
    assert rec.provenance["postExReturnD10Percent"] == 2.30245945
    assert rec.provenance["preExReturnBD10Percent"] == -4.24959656
    assert "not computed here" in rec.provenance["forwardReturnNote"]
    assert rec.posture.claim_type == "reported_metric", "a relay is not a fact"
    assert rec.posture.authority_level == "L5"
    assert rec.family == "issuer_disclosure"
    print("ok the vendor's post/pre-ex drift is reported rather than recomputed")


def test_missing_code_and_empty_history_are_labelled_gaps() -> None:
    try:
        dv.fetch_dividends("", as_of="2026-10-04")
    except net.FetchError as exc:
        assert "keyed by code" in str(exc)
    else:
        raise AssertionError("the report is keyed by code, so an empty code must be refused")
    with mock.patch.object(dv.net, "get_json", return_value={"result": {"data": []}}):
        try:
            dv.fetch_dividends("999999", as_of="2026-10-04")
        except net.FetchError as exc:
            message = str(exc)
            assert "dividend_source_gap" in message
            # A company that never declared one is an absence of plans, not a source failure.
            assert "never declared" in message and "check the code" in message
        else:
            raise AssertionError("no records must be a labelled gap")
    print("ok a missing code and an empty plan history are both labelled gaps")


def test_series_columns_match_the_declared_shape() -> None:
    series = _fetch([HUATAI, CASH_AND_SHARES]).series
    assert series["columns"] == dv.COLUMNS
    assert series["name"] == "dividends-601688"
    for row in series["rows"]:
        assert set(row.keys()) == set(dv.COLUMNS)
        assert not any(isinstance(v, (list, dict)) for v in row.values())
    print("ok series rows are flat and match the declared columns")


def main() -> int:
    test_dividend_ratio_is_a_ratio_and_becomes_a_percent()
    test_non_yield_ratios_stay_null_rather_than_becoming_zero()
    test_per_share_amount_is_not_the_per_ten_number()
    test_per_share_amount_survives_formatting()
    test_ex_dividend_days_is_a_signed_offset()
    test_forward_returns_are_reported_not_computed()
    test_missing_code_and_empty_history_are_labelled_gaps()
    test_series_columns_match_the_declared_shape()
    print("mira_data_dividend_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
