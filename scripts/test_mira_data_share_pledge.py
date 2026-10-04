#!/usr/bin/env python3
"""Offline tests for the A-share 股权质押 (share pledge) adapter.

Fixtures are the live rows measured 2026-10-04. The tests pin:

1. **The freshness trap**, which is the whole reason this adapter reports dates so insistently. A
   company that stops pledging simply stops appearing, so its newest row is its **last**
   observation rather than a current one: 万科A's is 2024-04-30 while 600519/000001/002415/300750
   are current to 2026-09-30. Reporting a stale row as current gives a two-year-old ratio.
2. **Absence is not zero.** A code missing from the register means CSDC lists no outstanding
   pledge — not that the ratio is 0%.
3. **Units are 万股/万元**, consistent with the other A-share relays and NOT the raw shares/yuan
   the block-trade detail report uses.
4. **The three views are distinct reports** with different shapes, and the market view must not
   be read as a per-stock row.
5. **The warning-state buckets are the vendor's**, reported as counts without asserting what each
   bucket means.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import net
from tools.mira_data.adapters import em_pledge as ep


def _stock(code, name, date, ratio):
    return {"SECUCODE": f"{code}.SZ", "SECURITY_CODE": code, "SECURITY_NAME_ABBR": name,
            "TRADE_DATE": f"{date} 00:00:00", "PLEDGE_RATIO": ratio,
            "REPURCHASE_BALANCE": 12000.48, "PLEDGE_DEAL_NUM": 7,
            "REPURCHASE_UNLIMITED_BALANCE": 12000.48, "REPURCHASE_LIMITED_BALANCE": 0,
            "PLEDGE_MARKET_CAP": 25580.0, "INDUSTRY": "房地产"}


STOCK_ROWS = [_stock("600519", "贵州茅台", "2026-09-30", 0.06),
              _stock("600519", "贵州茅台", "2026-09-24", 0.06)]
STALE_ROWS = [_stock("000002", "万科A", "2024-04-30", 1.23)]

MARKET_ROWS = [
    {"TRADE_DATE": "2026-09-30 00:00:00", "TOTAL_PLEDGED_SHARES": 28064095.49,
     "PLEDGE_MARKET_VALUE": 254794811.9815, "CSI_300_INDEX": 4357.6155,
     "CSI_300_CHG": -1.8366, "PM_RATIO": 270.7, "PLEDGE_CO_NUM": 2211,
     "DAILY_STATISTICS": 13401},
    {"TRADE_DATE": "2026-09-24 00:00:00", "TOTAL_PLEDGED_SHARES": 28122603.04,
     "PLEDGE_MARKET_VALUE": 254000000.0, "CSI_300_INDEX": 4400.0,
     "CSI_300_CHG": 0.5, "PM_RATIO": 270.0, "PLEDGE_CO_NUM": 2210,
     "DAILY_STATISTICS": 13000},
]

INSTITUTION_ROWS = [
    {"PFORG_CODE": "10004865", "SECURITY_NAME_ABBR": "国泰海通", "PFORG_TYPE": "证券Ⅱ",
     "ORG_NUM": 280, "PLEDGE_DEAL_NUM": 784, "PLEDGE_NUM": 995796.5757,
     "WARNING_STATE_1": 613, "WARNING_STATE_2": 30, "WARNING_STATE_3": 141,
     "WARNING_STATE_1_RATE": 0.78188775},
    {"PFORG_CODE": "10002269", "SECURITY_NAME_ABBR": "中信证券", "PFORG_TYPE": "证券Ⅱ",
     "ORG_NUM": 279, "PLEDGE_DEAL_NUM": 708, "PLEDGE_NUM": 1788514.8635,
     "WARNING_STATE_1": 504, "WARNING_STATE_2": 43, "WARNING_STATE_3": 161,
     "WARNING_STATE_1_RATE": 0.7118644},
]


def _fetch(rows, **kwargs):
    payload = {"result": {"count": len(rows), "data": rows}}
    with mock.patch.object(ep.net, "get_json", return_value=payload) as getter:
        result = ep.fetch_share_pledge(as_of="2026-10-04", **kwargs)
    return result, getter


def test_stock_view_reports_the_date_and_freshness() -> None:
    result, _ = _fetch(STOCK_ROWS, symbol="600519", view="stock")
    rec = result.records[0]
    assert rec.value == 0.06 and rec.unit == "percent"
    assert rec.period == "2026-09-30"
    assert rec.provenance["lastObservedDate"] == "2026-09-30"
    assert rec.provenance["stalenessDays"] == 4, rec.provenance["stalenessDays"]
    assert rec.posture.authority_level == "L5"
    assert rec.posture.source_id == "eastmoney_share_pledge_api"
    assert "2026-09-30" in rec.claim_text
    print("ok the stock view reports its observation date and staleness")


def test_a_stale_observation_is_flagged_not_presented_as_current() -> None:
    # 万科A's register entry ends in 2024; that is its LAST observation, not today's ratio.
    result, _ = _fetch(STALE_ROWS, symbol="000002", view="stock")
    rec = result.records[0]
    assert rec.period == "2024-04-30"
    assert rec.provenance["stalenessDays"] > 700, rec.provenance["stalenessDays"]
    assert "LAST observed ratio" in rec.provenance["stalenessNote"]
    # The date is in the claim text, so the staleness cannot be lost downstream.
    assert "2024-04-30" in rec.claim_text, rec.claim_text
    print("ok a stale observation carries its date and is not presented as current")


def test_absence_from_the_register_is_not_a_zero() -> None:
    with mock.patch.object(ep.net, "get_json", return_value={"result": {"data": []}}):
        try:
            ep.fetch_share_pledge("601688", view="stock", as_of="2026-10-04")
        except net.FetchError as exc:
            message = str(exc)
            assert "pledge_source_gap" in message
            # The whole point: absence must not be readable as a 0% pledge ratio.
            assert "not one with zero pledges" in message
            assert "do not read the absence as a zero" in message
        else:
            raise AssertionError("a code absent from the register must be a labelled gap")
    print("ok absence from the register is a labelled gap, not a zero ratio")


def test_units_are_wan_and_stated() -> None:
    stock, _ = _fetch(STOCK_ROWS, symbol="600519", view="stock")
    assert "万股" in stock.records[0].provenance["unitNote"]
    assert "not the raw shares/yuan" in stock.records[0].provenance["unitNote"]
    market, _ = _fetch(MARKET_ROWS, view="market")
    assert market.records[0].unit == "10k_shares"
    assert "万股" in market.records[0].provenance["unitNote"]
    print("ok the 万股/万元 convention is used and stated")


def test_market_view_claims_the_whole_market_row() -> None:
    result, _ = _fetch(MARKET_ROWS, view="market")
    rec = result.records[0]
    assert rec.research_object == "PLEDGE_MARKET"
    assert rec.value == 28064095.49
    assert rec.period == "2026-09-30"
    assert rec.provenance["pledgingCompanies"] == 2211
    assert rec.provenance["csi300"] == 4357.6155
    assert "2,211" in rec.claim_text, rec.claim_text
    # The history travels so a trend is readable, not just the single row.
    assert len(result.series["rows"]) == 2
    assert result.series["name"] == "share-pledge-market"
    print("ok the market view claims the market row and carries its history")


def test_institution_view_reports_warning_counts_without_defining_them() -> None:
    result, _ = _fetch(INSTITUTION_ROWS, view="institution")
    assert [r.provenance["name"] for r in result.records] == ["国泰海通", "中信证券"]
    first = result.records[0]
    assert first.provenance["warningState1"] == 613
    assert first.provenance["warningState2"] == 30
    assert first.provenance["warningState3"] == 141
    # The buckets are the vendor's; their definitions are deliberately not asserted.
    assert "vendor's own classification" in first.provenance["warningNote"]
    assert "NOT asserted" in first.provenance["warningNote"]
    assert first.unit == "10k_shares"
    print("ok the institution view reports warning counts without asserting their meaning")


def test_views_and_missing_code_are_refused_clearly() -> None:
    try:
        ep.fetch_share_pledge("600519", view="pledges", as_of="2026-10-04")
    except net.FetchError as exc:
        message = str(exc)
        assert "pledge_view_gap" in message
        for good in ep.VIEWS:
            assert good in message, (good, message)
    else:
        raise AssertionError("an unknown view must be refused")
    # The per-stock view without a code is a usage error, and says which views are market-wide.
    try:
        ep.fetch_share_pledge("", view="stock", as_of="2026-10-04")
    except net.FetchError as exc:
        message = str(exc)
        assert "needs an A-share code" in message
        assert "view=market" in message and "view=institution" in message
    else:
        raise AssertionError("the stock view without a code must be refused")
    print("ok an unknown view and a missing code are both refused with guidance")


def test_endpoint_is_literal_and_series_shapes_match() -> None:
    # A stray placeholder here would KeyError at format time, which is how two families shipped
    # with a broken endpoint before the wiring test existed.
    assert "{" not in ep.ENDPOINT and "}" not in ep.ENDPOINT
    for rows, kwargs, columns in (
        (STOCK_ROWS, {"symbol": "600519", "view": "stock"}, ep.STOCK_COLUMNS),
        (MARKET_ROWS, {"view": "market"}, ep.MARKET_COLUMNS),
        (INSTITUTION_ROWS, {"view": "institution"}, ep.INSTITUTION_COLUMNS),
    ):
        series = _fetch(rows, **kwargs)[0].series
        assert series["columns"] == columns
        for row in series["rows"]:
            assert set(row.keys()) == set(columns), set(row) ^ set(columns)
            assert not any(isinstance(v, (list, dict)) for v in row.values())
    print("ok the endpoint is literal and every view's series matches its declared columns")


def main() -> int:
    test_stock_view_reports_the_date_and_freshness()
    test_a_stale_observation_is_flagged_not_presented_as_current()
    test_absence_from_the_register_is_not_a_zero()
    test_units_are_wan_and_stated()
    test_market_view_claims_the_whole_market_row()
    test_institution_view_reports_warning_counts_without_defining_them()
    test_views_and_missing_code_are_refused_clearly()
    test_endpoint_is_literal_and_series_shapes_match()
    print("mira_data_share_pledge_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
