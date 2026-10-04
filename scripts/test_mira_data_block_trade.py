#!/usr/bin/env python3
"""Offline tests for the A-share 大宗交易 (block trade) adapter.

This dataset carries **two unit traps**, and both were established by arithmetic against live
rows rather than by trusting a vendor label:

1. **``PREMIUM_RATIO`` is a ratio, not a percent.** 0.001390820584 is a 0.14% premium and
   -0.135181 is a **-13.5%** discount. Publishing it raw would report a 13.5% discount as
   -0.14% — wrong by two orders of magnitude and still plausible-looking.
2. **The two reports use different units.** In the detail report ``DEAL_VOLUME``/``DEAL_AMT``
   are raw shares and yuan (proven: ``DEAL_PRICE × DEAL_VOLUME`` reproduces ``DEAL_AMT``). In the
   stats report ``VOLUME`` is **万股** and ``DEAL_AMT`` is **万元** (proven: ``AMT/VOL``
   reproduces ``AVERAGE_PRICE``). Reading the stats figures as raw understates a 56,032,600-yuan
   trade as 5,603,260 — an off-by-10,000 error that still looks like a plausible block size.

The fixture values are the live rows for 2026-09-30.
"""

from __future__ import annotations

import sys
import urllib.parse
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import net
from tools.mira_data.adapters import em_block_trade as bt


# Detail report: raw shares/yuan. 120.46 x 465,200 = 56,037,992 against a reported 56,032,600.
DETAIL_ROWS = [
    {"SECURITY_CODE": "002028", "SECURITY_NAME_ABBR": "思源电气", "TRADE_DATE": "2026-09-30 00:00:00",
     "DEAL_PRICE": 120.46, "DEAL_VOLUME": 465200, "DEAL_AMT": 56032600,
     "PREMIUM_RATIO": 0, "CLOSE_PRICE": 120.46,
     "BUYER_NAME": "中信证券股份有限公司总部", "SELLER_NAME": "机构专用", "TRADE_UNIT": 4},
    {"SECURITY_CODE": "300951", "SECURITY_NAME_ABBR": "博硕科技", "TRADE_DATE": "2026-09-30 00:00:00",
     "DEAL_PRICE": 70.82, "DEAL_VOLUME": 280000, "DEAL_AMT": 19829600,
     "PREMIUM_RATIO": -0.135181340823, "CLOSE_PRICE": 81.89,
     "BUYER_NAME": "某营业部", "SELLER_NAME": "某营业部", "TRADE_UNIT": 4},
    {"SECURITY_CODE": "159967", "SECURITY_NAME_ABBR": "华夏创成长ETF",
     "TRADE_DATE": "2026-09-30 00:00:00",
     "DEAL_PRICE": 0.72, "DEAL_VOLUME": 190821300, "DEAL_AMT": 137773000,
     "PREMIUM_RATIO": 0.001390820584, "CLOSE_PRICE": 0.719,
     "BUYER_NAME": "某机构", "SELLER_NAME": "某机构", "TRADE_UNIT": 3},
]

# Stats report: 万股 / 万元, proven by AMT/VOL == AVERAGE_PRICE.
STATS_ROWS = [
    {"SECURITY_CODE": "002028", "SECURITY_NAME_ABBR": "思源电气", "TRADE_DATE": "2026-09-30 00:00:00",
     "DEAL_NUM": 2, "VOLUME": 72.27, "DEAL_AMT": 8705.06, "AVERAGE_PRICE": 120.4519,
     "CLOSE_PRICE": 120.46, "PREMIUM_RATIO": -0.0000663, "CHANGE_RATE": 0.0,
     "D1_CLOSE_ADJCHRATE": None, "D5_CLOSE_ADJCHRATE": None,
     "D10_CLOSE_ADJCHRATE": None, "D20_CLOSE_ADJCHRATE": None},
    {"SECURITY_CODE": "300951", "SECURITY_NAME_ABBR": "博硕科技",
     "TRADE_DATE": "2026-09-30 00:00:00",
     "DEAL_NUM": 7, "VOLUME": 56.77, "DEAL_AMT": 4020.45, "AVERAGE_PRICE": 70.82,
     "CLOSE_PRICE": 81.89, "PREMIUM_RATIO": -0.135181340823, "CHANGE_RATE": -1.5,
     "D1_CLOSE_ADJCHRATE": -2.0, "D5_CLOSE_ADJCHRATE": 1.5,
     "D10_CLOSE_ADJCHRATE": None, "D20_CLOSE_ADJCHRATE": None},
]


def _fetch(rows, **kwargs):
    payload = {"result": {"count": len(rows), "data": rows}}
    with mock.patch.object(bt.net, "get_json", return_value=payload):
        return bt.fetch_block_trades("", date="2026-09-30", as_of="2026-10-04", **kwargs)


def test_premium_ratio_is_converted_to_percent() -> None:
    result = _fetch(DETAIL_ROWS, view="detail")
    by_code = {r.research_object: r for r in result.records}
    # The 13.5% discount must not surface as -0.14%.
    bs = by_code["300951"]
    assert bs.provenance["premiumRatioAsReturned"] == -0.135181340823
    assert abs(bs.provenance["premiumPercent"] - (-13.5181340823)) < 1e-6
    assert "-13.52%" in bs.claim_text or "-13.5" in bs.claim_text, bs.claim_text
    # The tiny premium must read as a small positive, not vanish.
    huaxia = by_code["159967"]
    assert huaxia.provenance["premiumPercent"] is not None
    assert 0.13 < huaxia.provenance["premiumPercent"] < 0.15
    # A genuine zero premium stays zero rather than becoming None.
    assert by_code["002028"].provenance["premiumPercent"] == 0.0
    assert "RATIO" in bs.provenance["premiumUnitNote"]
    print("ok PREMIUM_RATIO is converted from ratio to percent, and a zero stays zero")


def test_detail_view_uses_raw_units() -> None:
    rec = {r.research_object: r for r in _fetch(DETAIL_ROWS, view="detail").records}["002028"]
    assert rec.provenance["dealVolumeShares"] == 465200, rec.provenance
    assert rec.provenance["dealAmountYuan"] == 56032600, rec.provenance
    assert rec.value == 56032600.0 and rec.unit == "CNY"
    # The arithmetic proof must travel, since it is the only reason to trust the unit.
    assert "DEAL_PRICE x DEAL_VOLUME" in rec.provenance["unitBasis"]
    assert "SHARES" in rec.provenance["unitBasis"]
    print("ok the detail view keeps raw shares and yuan, with the arithmetic proof recorded")


def test_stats_view_is_rescaled_from_wan() -> None:
    rec = {r.research_object: r for r in _fetch(STATS_ROWS, view="stats").records}["002028"]
    # 8705.06 万元 is 87,050,600 yuan -- not 8,705.
    assert rec.value == 87050600.0, rec.value
    assert rec.provenance["amountYuan"] == 87050600.0
    assert rec.provenance["volume"] == 722700.0, "72.27 万股 is 722,700 shares"
    # The vendor's raw values are kept so the scaling can be checked independently.
    assert rec.provenance["amountAsReturned"] == 8705.06
    assert rec.provenance["volumeAsReturned"] == 72.27
    assert "万股" in rec.provenance["unitBasis"] and "万元" in rec.provenance["unitBasis"]
    assert "87,050,600" in rec.claim_text, rec.claim_text
    print("ok the stats view is rescaled from 万股/万元 and keeps the vendor values")


def test_stats_view_reports_forward_drift_without_inventing_it() -> None:
    rec = {r.research_object: r for r in _fetch(STATS_ROWS, view="stats").records}["300951"]
    assert rec.provenance["forwardReturnD1"] == -2.0
    assert rec.provenance["forwardReturnD5"] == 1.5
    # A horizon that has not elapsed stays null rather than becoming zero return.
    assert rec.provenance["forwardReturnD10"] is None
    assert rec.provenance["forwardReturnD20"] is None
    assert "not computed here" in rec.provenance["forwardReturnNote"]
    print("ok forward drift is reported, and an unelapsed horizon stays null")


def test_views_and_empty_results_are_labelled_gaps() -> None:
    try:
        bt.fetch_block_trades("", view="prints", as_of="2026-10-04")
    except net.FetchError as exc:
        message = str(exc)
        assert "block_trade_view_gap" in message
        for good in ("detail", "stats"):
            assert good in message, (good, message)
    else:
        raise AssertionError("an unknown view must be refused")
    for rows, view in (([], "detail"), ([], "stats")):
        payload = {"result": {"count": 0, "data": rows}}
        with mock.patch.object(bt.net, "get_json", return_value=payload):
            try:
                bt.fetch_block_trades("", date="2026-09-30", view=view, as_of="2026-10-04")
            except net.FetchError as exc:
                assert "block_trade_source_gap" in str(exc)
                assert "widen the date" in str(exc)
            else:
                raise AssertionError("an empty session must be a labelled gap")
    print("ok an unknown view and an empty session are both labelled gaps")


def test_the_filter_is_sent_and_the_endpoint_has_no_placeholder() -> None:
    with mock.patch.object(bt.net, "get_json", return_value={"result": {"data": DETAIL_ROWS}}) as g:
        bt.fetch_block_trades("002028", date="20260930", as_of="2026-10-04")
    url = g.call_args[0][0]
    assert "RPT_DATA_BLOCKTRADE" in url
    decoded = urllib.parse.unquote_plus(url)
    assert "SECURITY_CODE=\"002028\"" in decoded, decoded
    assert "TRADE_DATE='2026-09-30'" in decoded, "an 8-digit date must be normalised to ISO"
    # The manifest records this literal, so a stray {placeholder} would KeyError at format time.
    assert "{" not in bt.ENDPOINT and "}" not in bt.ENDPOINT
    print("ok the filter is sent correctly, the date is normalised, and the endpoint is literal")


def test_series_columns_match_the_declared_shape() -> None:
    for rows, view, columns in ((DETAIL_ROWS, "detail", bt.DETAIL_COLUMNS),
                                (STATS_ROWS, "stats", bt.STATS_COLUMNS)):
        series = _fetch(rows, view=view).series
        assert series["columns"] == columns
        for row in series["rows"]:
            assert set(row.keys()) == set(columns)
            assert not any(isinstance(v, (list, dict)) for v in row.values())
    print("ok both views emit flat series rows matching their declared columns")


def main() -> int:
    test_premium_ratio_is_converted_to_percent()
    test_detail_view_uses_raw_units()
    test_stats_view_is_rescaled_from_wan()
    test_stats_view_reports_forward_drift_without_inventing_it()
    test_views_and_empty_results_are_labelled_gaps()
    test_the_filter_is_sent_and_the_endpoint_has_no_placeholder()
    test_series_columns_match_the_declared_shape()
    print("mira_data_block_trade_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
