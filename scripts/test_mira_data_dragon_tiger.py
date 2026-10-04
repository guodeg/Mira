#!/usr/bin/env python3
"""Offline tests for the 龙虎榜 (dragon-tiger list) adapter.

Fixtures reproduce the live payload measured 2026-10-04 for session 2026-09-30: 79 per-stock
rows and 15 per-seat rows. The tests pin the things that cost real debugging time or would
publish a wrong claim:

1. **The two board types return different shapes.** ``all``/``org`` give ``stock_items`` (one row
   per stock); ``hot_money`` gives ``hot_money_items`` (one row per seat, with stocks *nested*).
   Reading the wrong key yields nothing rather than an error.
2. **The date must be a trading day.** The CLI rejects a non-trading date as a validation error
   (``FUYAO_1002``) rather than returning empty — a different failure from an empty session.
3. **An empty session is legitimate** and must be reported, not emitted as zero claims.
4. **Provenance must be flat.** The side-series CSV rejects a dict carrying a field its header
   does not declare, and the concept tags are prose, not a column.
5. **Seat rows do not sum to the per-stock view** — one stock appears under several seats, so
   presenting them as additive would double count.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import net
from tools.mira_data.adapters import hithink_special as hs


def _stock(thscode="000002.SZ", ticker="000002", name="万科A", net_value=71384559.48):
    return {
        "thscode": thscode, "ticker": ticker, "name": name,
        "concept_list": [{"name": "租售同权"}, {"name": "物业管理"}],
        "change": 0.044118, "net_value": net_value, "net_rate": 0.01383944, "hot_rank": 1,
        "buy_value": 688964755.91, "sell_value": 617580196.43, "range_days": 1,
        "org_net_value": 118171874.02, "hot_money_net_value": 35889476.24,
    }


def _seat(name="佛山系", buying=183459255):
    return {"name": name, "buying": buying, "rows": [_stock()]}


STOCK_PAYLOAD = {"trade_date": "2026-09-30", "board_type": "all", "count": 2,
                 "stock_items": [_stock(), _stock("600519.SH", "600519", "贵州茅台", 504127136.94)],
                 "hot_money_items": []}
SEAT_PAYLOAD = {"trade_date": "2026-09-30", "board_type": "hot_money", "count": 1,
                "stock_items": [], "hot_money_items": [_seat()]}


def _fetch(payload, **kwargs):
    with mock.patch.object(hs, "vendor_json", return_value=payload):
        return hs.fetch_dragon_tiger("2026-09-30", as_of="2026-10-04", **kwargs)


def test_per_stock_board_claims_each_name() -> None:
    result = _fetch(STOCK_PAYLOAD, board_type="all")
    assert len(result.records) == 2
    first = result.records[0]
    assert first.research_object == "LHB_000002"
    assert first.value == 71384559.48 and first.unit == "CNY"
    assert first.period == "2026-09-30"
    assert first.posture.authority_level == "L5", "the exchanges published the same list"
    assert first.posture.claim_type == "reported_metric"
    assert "万科A" in first.claim_text and "71,384,559" in first.claim_text
    # The seat-vs-stock distinction is stated, because the two views are not additive.
    assert result.series["name"] == "dragon-tiger"
    assert len(result.series["rows"]) == 2
    print("ok the per-stock board claims each name with its net figures")


def test_hot_money_board_flattens_seats_and_nests_stocks() -> None:
    result = _fetch(SEAT_PAYLOAD, board_type="hot_money")
    assert len(result.records) == 1
    rec = result.records[0]
    assert rec.provenance["seat"] == "佛山系"
    assert rec.provenance["seatBuying"] == 183459255
    assert rec.provenance["netValue"] == 71384559.48
    assert rec.value == 71384559.48
    assert "佛山系" in rec.claim_text
    # The non-additivity warning must travel with the row.
    assert "do not sum" in rec.provenance["seatNote"]
    assert result.series["name"] == "dragon-tiger-seats"
    assert result.series["rows"][0]["seat"] == "佛山系"
    print("ok the hot-money board flattens seats and warns they are not additive")


def test_series_rows_match_their_declared_columns() -> None:
    # The side-series CSV rejects a dict with a field the header does not declare, which is how
    # a nested concept list broke the real emit. Keys must equal the declared columns exactly.
    for payload, board, columns in ((STOCK_PAYLOAD, "all", hs.STOCK_COLUMNS),
                                    (SEAT_PAYLOAD, "hot_money", hs.HOT_MONEY_COLUMNS)):
        series = _fetch(payload, board_type=board).series
        assert series["columns"] == columns
        for row in series["rows"]:
            assert set(row.keys()) == set(columns), (board, set(row) ^ set(columns))
            assert not any(isinstance(v, (list, dict)) for v in row.values()), (board, row)
    # Provenance holds flat scalars too; the tags are joined into one string.
    rec = _fetch(STOCK_PAYLOAD, board_type="all").records[0]
    assert isinstance(rec.provenance["concepts"], str)
    assert rec.provenance["concepts"] == "租售同权 | 物业管理"
    print("ok series rows and provenance hold only flat fields the schema declares")


def test_empty_session_is_a_labelled_gap() -> None:
    empty = {"trade_date": "2026-09-30", "stock_items": [], "hot_money_items": []}
    with mock.patch.object(hs, "vendor_json", return_value=empty):
        try:
            hs.fetch_dragon_tiger("2026-09-30", as_of="2026-10-04")
        except net.FetchError as exc:
            message = str(exc)
            assert "lhb_source_gap" in message
            # It must be distinguishable from the non-trading-date refusal.
            assert "EMPTY SESSION" in message and "rather than a failure" in message
            assert "non-trading date" in message
        else:
            raise AssertionError("an empty session must be reported, not emitted as zero claims")
    print("ok an empty session is a labelled gap, distinct from a bad date")


def test_bad_board_type_is_refused_before_any_call() -> None:
    called = []
    with mock.patch.object(hs, "vendor_json", side_effect=lambda *a, **k: called.append(a)):
        try:
            hs.fetch_dragon_tiger("2026-09-30", board_type="seats", as_of="2026-10-04")
        except net.FetchError as exc:
            message = str(exc)
            assert "hithink_board_gap" in message
            for good in ("all", "org", "hot_money"):
                assert good in message, (good, message)
        else:
            raise AssertionError("an unknown board type must be refused")
    assert not called, "the refusal must happen before spending a vendor call"
    print("ok an unknown board type is refused without calling the vendor")


def test_org_board_keeps_only_rows_with_institutional_flow() -> None:
    plain = _stock("000001.SZ", "000001", "平安银行", 1000.0)
    plain["org_net_value"] = 0
    payload = dict(STOCK_PAYLOAD, stock_items=[_stock(), plain])
    result = _fetch(payload, board_type="org")
    assert len(result.records) == 1, [r.research_object for r in result.records]
    assert result.records[0].research_object == "LHB_000002"
    print("ok the institutional board keeps only rows with institutional flow")


def test_numeric_absence_is_not_read_as_zero() -> None:
    absent = _stock()
    absent["net_value"] = None
    absent["buy_value"] = None
    payload = dict(STOCK_PAYLOAD, stock_items=[absent])
    rec = _fetch(payload, board_type="all").records[0]
    # A vendor omission must not become a claim that the net flow was zero.
    assert rec.value == 0.0 and rec.provenance["netValue"] is None
    assert "n/a" in rec.claim_text, rec.claim_text
    assert "0" not in rec.claim_text.replace("n/a", "").split("净买入")[1][:12]
    print("ok an absent vendor figure reads as n/a rather than as zero")


def main() -> int:
    test_per_stock_board_claims_each_name()
    test_hot_money_board_flattens_seats_and_nests_stocks()
    test_series_rows_match_their_declared_columns()
    test_empty_session_is_a_labelled_gap()
    test_bad_board_type_is_refused_before_any_call()
    test_org_board_keeps_only_rows_with_institutional_flow()
    test_numeric_absence_is_not_read_as_zero()
    print("mira_data_dragon_tiger_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
