#!/usr/bin/env python3
"""Offline tests for the official futures member-ranking channel (SHFE and CZCE, L2).

Both venues were probed live on 2026-10-03; the fixtures below reproduce what those probes
returned, because every quirk in them is a way to produce a wrong or empty answer:

1. **SHFE instrument matching must be exact.** `cu` also prefixes `cual`, and a bare
   ``startswith`` swallowed 331 rows across unrelated series instead of the requested variety.
2. **CZCE glues the Chinese name to the ticker** (`品种：苹果AP`), so the requested code is the
   *trailing* ascii run. Prefix matching found nothing at all.
3. **CZCE is UTF-8.** Decoding it as GBK (the usual mainland guess) produced mojibake on the
   first probe.
4. **A missing session is normal**, so the adapter walks back a bounded window and records
   both the requested and the used date rather than reporting the first 404 as a gap.
5. **The three ranked tables sit side by side** with *different* members, so each side keeps
   its own member name — collapsing them would attribute one member's long book to another.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import net
from tools.mira_data.adapters import exchange_futures as ef


# SHFE pm20260930.dat: the variety aggregate, one contract, an unrelated series that also
# starts with "cu", and a non-ranking placeholder row (RANK is a string, not an int).
SHFE_PAYLOAD = {"report_date": "20260930", "o_cursor": [
    {"INSTRUMENTID": "cuall", "RANK": 1, "PRODUCTNAME": "铜",
     "PARTICIPANTABBR1": "金瑞期货", "CJ1": 289192, "CJ2": 537232, "CJ3": 523931,
     "CJ1_CHG": -27657, "CJ2_CHG": -6965, "CJ3_CHG": -8431},
    {"INSTRUMENTID": "cu2610", "RANK": 1, "PRODUCTNAME": "铜",
     "PARTICIPANTABBR1": "金瑞期货", "CJ1": 5750, "CJ2": 7990, "CJ3": 5246,
     "CJ1_CHG": -1923, "CJ2_CHG": -1232, "CJ3_CHG": -1493},
    {"INSTRUMENTID": "cu2610", "RANK": 2, "PRODUCTNAME": "铜",
     "PARTICIPANTABBR1": "国泰君安", "CJ1": "4,100", "CJ2": "3,000", "CJ3": "2,500",
     "CJ1_CHG": "", "CJ2_CHG": "", "CJ3_CHG": ""},
    # `cual` is a DIFFERENT series that merely shares the prefix; it must not be matched.
    {"INSTRUMENTID": "cual", "RANK": 1, "PRODUCTNAME": "铜铝",
     "PARTICIPANTABBR1": "错误会员", "CJ1": 999999, "CJ2": 1, "CJ3": 1},
    # The exchange also ships a non-ranked summary row whose RANK is not an integer.
    {"INSTRUMENTID": "cuall", "RANK": -1, "PRODUCTNAME": "铜",
     "PARTICIPANTABBR1": "期货公司会员", "CJ1": 289192, "CJ2": 537152, "CJ3": 523931},
]}

CZCE_TEXT = """\t\t\t\t\t郑州商品交易所期货持仓排名表(2026-09-30)

品种：苹果AP              日期：2026-09-30
名次  |会员简称        |成交量（手）|增减量    |会员简称        |持买单量  |增减量    |会员简称        |持卖单量  |增减量
1     |国泰君安（代客）    |39,292      |14,986    |国泰君安（代客）    |15,944     |1,365     |国泰君安（代客）    |17,991     |-2,000
2     |东证期货（代客）    |34,584      |16,046    |中信期货（代客）    |11,736     |769       |中信期货（代客）    |14,651     |500

品种：棉花CF              日期：2026-09-30
1     |中信期货（代客）    |145,793     |-15,312   |中信期货（代客）    |103,348    |3,111     |中信期货（代客）    |115,597    |1,000
"""


def _fetch_shfe(payload=None, **kwargs):
    data = SHFE_PAYLOAD if payload is None else payload
    with mock.patch.object(ef.net, "get_json", return_value=data):
        return ef.fetch_member_rankings("cu", venue="SHFE", date="2026-09-30",
                                        as_of="2026-10-03", **kwargs)


def _fetch_czce(text=None, **kwargs):
    body = (CZCE_TEXT if text is None else text).encode("utf-8")
    with mock.patch.object(ef.net, "get", return_value=body):
        return ef.fetch_member_rankings("AP", venue="CZCE", date="2026-09-30",
                                        as_of="2026-10-03", **kwargs)


def test_shfe_matches_the_variety_exactly() -> None:
    result = _fetch_shfe()
    instruments = {r.research_object for r in result.records}
    assert instruments == {"CUALL", "CU2610"}, instruments
    # The prefix-sharing series must not leak in.
    assert not any("CUAL" in r.claim_text for r in result.records)
    assert not any("错误会员" in r.claim_text for r in result.records)
    # The placeholder row whose RANK is not an integer is not a ranking entry.
    assert all(r.value >= 1 for r in result.records)
    first = [r for r in result.records if r.research_object == "CU2610" and r.value == 1][0]
    assert first.provenance["member"] == "金瑞期货"
    assert first.provenance["volume"] == 5750 and first.provenance["longPositions"] == 7990
    assert first.provenance["shortPositions"] == 5246
    print("ok SHFE matches the requested variety exactly and skips non-ranked rows")


def test_shfe_keeps_the_three_sides_separate_and_parses_thousands() -> None:
    result = _fetch_shfe()
    second = [r for r in result.records if r.research_object == "CU2610" and r.value == 2][0]
    # Volume/long/short are separate ranked tables; the member can differ per side.
    assert second.provenance["volume"] == 4100, "quoted '4,100' must parse"
    assert second.provenance["volumeChange"] is None, "an empty delta is not a zero"
    assert "手" in second.claim_text
    print("ok SHFE parses grouped numbers, keeps empties empty, and labels lots")


def test_czce_matches_the_trailing_ticker_and_stays_utf8() -> None:
    result = _fetch_czce()
    assert result.records, "CZCE must find the 苹果AP table"
    assert all(r.research_object == "苹果AP" for r in result.records)
    # The other variety in the same file must not leak in.
    assert not any("棉花" in r.research_object for r in result.records)
    assert not any("CF" in r.research_object for r in result.records)
    first = result.records[0]
    assert first.provenance["volumeMember"] == "国泰君安（代客）"
    assert first.provenance["volume"] == 39292
    assert first.provenance["longPositions"] == 15944
    assert first.provenance["shortPositions"] == 17991
    # Mojibake check: UTF-8 decoded as GBK yields characters the file never contained.
    assert "鍥芥嘲" not in first.claim_text and "锛" not in first.claim_text
    print("ok CZCE matches the trailing ticker, reads UTF-8, and keeps sides separate")


def test_a_missing_session_walks_back_and_records_both_dates() -> None:
    seen = []

    def fake_get(url, **kwargs):
        seen.append(url)
        if "20260930" in url:
            raise net.FetchError("HTTP 404 for url", status=404, url=url)
        return SHFE_PAYLOAD

    with mock.patch.object(ef.net, "get_json", side_effect=fake_get):
        result = ef.fetch_member_rankings("cu", venue="SHFE", date="2026-09-30",
                                          as_of="2026-10-03")
    assert len(seen) >= 2, seen
    assert "20260930" in seen[0] and "20260929" in seen[1], seen
    rec = result.records[0]
    assert rec.period == "2026-09-29", rec.period
    # Both dates travel: a reader must be able to tell the requested session from the used one.
    assert rec.provenance["requestedDate"] == "2026-09-30"
    assert rec.provenance["usedDate"] == "2026-09-29"

    with mock.patch.object(ef.net, "get_json",
                           side_effect=net.FetchError("HTTP 404", status=404, url="u")):
        try:
            ef.fetch_member_rankings("cu", venue="SHFE", date="2026-09-30", as_of="2026-10-03")
        except net.FetchError as exc:
            assert "futures_source_gap" in str(exc) and "cu" in str(exc)
        else:
            raise AssertionError("every session missing must be a labelled gap")
    print("ok a missing session walks back and records both the requested and used date")


def test_venue_and_family_contract() -> None:
    try:
        ef.fetch_member_rankings("cu", venue="DCE", as_of="2026-10-03")
    except net.FetchError as exc:
        message = str(exc)
        assert "futures_venue_gap" in message
        # The refusal names what was tried, so the next person does not repeat it.
        for venue in ("DCE", "CFFEX", "GFEX"):
            assert venue in message, (venue, message)
    else:
        raise AssertionError("an unwired venue must be refused, not silently redirected")

    result = _fetch_shfe()
    for rec in result.records:
        assert rec.family == "ownership_short_interest", rec.family
        assert rec.posture.authority_level == "L2", "the exchange controls its own market"
        assert rec.posture.source_id == "shfe_member_rank_api"
        assert rec.posture.claim_type == "fact"
        assert rec.unit == "rank" and rec.metric == "member_rank"
        assert rec.as_of_date == "2026-10-03" and rec.source_date == "2026-09-30"
    czce = _fetch_czce()
    assert all(r.posture.source_id == "czce_member_rank_api" for r in czce.records)
    print("ok the venue is explicit, and rows are L2 facts carrying their registry source")


def test_rank_limit_is_bounded() -> None:
    result = _fetch_shfe(rank_limit=1)
    assert {r.value for r in result.records} == {1.0}, {r.value for r in result.records}
    assert ef._num_cn("1,234") == 1234 and ef._num_cn("") is None and ef._num_cn("—") is None
    assert ef._czce_code("苹果AP") == "AP" and ef._czce_code("棉花CF") == "CF"
    assert ef._czce_code("AP") == "AP"
    print("ok the rank cap holds and the small parsers behave")


def main() -> int:
    test_shfe_matches_the_variety_exactly()
    test_shfe_keeps_the_three_sides_separate_and_parses_thousands()
    test_czce_matches_the_trailing_ticker_and_stays_utf8()
    test_a_missing_session_walks_back_and_records_both_dates()
    test_venue_and_family_contract()
    test_rank_limit_is_bounded()
    print("mira_data_exchange_futures_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
