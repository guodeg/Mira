#!/usr/bin/env python3
"""Offline tests for the FINRA consolidated short interest adapter (L2, keyless).

This channel **corrects the gap list**, which had recorded US short interest as having no free
source. The four contract details below each cost a live probe, and every one produces a
plausible wrong answer rather than an error:

1. **The endpoint answers CSV, not JSON** despite being Socrata-shaped.
2. **Filtering is POST-only with a JSON body.** GET parameters are ignored, so a GET carrying a
   symbol filter returns the *unfiltered* first page — which reads as a successful filtered
   fetch.
3. **Rows arrive ascending by settlement date**, and `sortFields` is rejected with HTTP 400. The
   newest settlement is therefore the *last* row; taking the first would publish a 2020 figure
   as the current position.
4. **One settlement date arrives per query**, limit-capped, so a market-wide sweep is a sample
   of that batch rather than the whole market — and must say so.

The fixture rows are trimmed from the live 2026-08-31 and 2026-09-15 batches.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import net
from tools.mira_data.adapters import finra_short as fs


def _csv(rows) -> str:
    header = ("accountingYearMonthNumber,symbolCode,issueName,issuerServicesGroupExchangeCode,"
              "marketClassCode,currentShortPositionQuantity,previousShortPositionQuantity,"
              "averageDailyVolumeQuantity,daysToCoverQuantity,changePercent,"
              "changePreviousNumber,settlementDate")
    out = [header]
    for r in rows:
        out.append(",".join(str(r.get(k, "")) for k in header.split(",")))
    return "\n".join(out) + "\n"


# Ascending order, as the API really returns: the 2020 settlement comes first.
AAPL_ROWS = [
    {"accountingYearMonthNumber": "20200415", "symbolCode": "AAPL",
     "issueName": "Apple Inc. Common Stock", "marketClassCode": "NNM",
     "currentShortPositionQuantity": 34636195, "previousShortPositionQuantity": 39059449,
     "averageDailyVolumeQuantity": 38275532, "daysToCoverQuantity": 1.0,
     "changePercent": -11.32, "changePreviousNumber": -4423254, "settlementDate": "2020-04-15"},
    {"accountingYearMonthNumber": "20260831", "symbolCode": "AAPL",
     "issueName": "Apple Inc. Common Stock", "marketClassCode": "NNM",
     "currentShortPositionQuantity": 139749097, "previousShortPositionQuantity": 140000000,
     "averageDailyVolumeQuantity": 40000000, "daysToCoverQuantity": 3.53,
     "changePercent": -0.18, "changePreviousNumber": -250903, "settlementDate": "2026-08-31"},
    {"accountingYearMonthNumber": "20260915", "symbolCode": "AAPL",
     "issueName": "Apple Inc. Common Stock", "marketClassCode": "NNM",
     "currentShortPositionQuantity": 128753092, "previousShortPositionQuantity": 139749097,
     "averageDailyVolumeQuantity": 42000000, "daysToCoverQuantity": 3.0,
     "changePercent": -7.87, "changePreviousNumber": -10996005, "settlementDate": "2026-09-15"},
]


def _fetch(rows=None, **kwargs):
    body = _csv(AAPL_ROWS if rows is None else rows)
    with mock.patch.object(fs.urllib.request, "urlopen") as opener:
        opener.return_value.__enter__.return_value.read.return_value = body.encode("utf-8")
        return fs.fetch_short_interest("AAPL", as_of="2026-10-04", **kwargs)


def test_newest_settlement_wins_and_not_the_first_row() -> None:
    result = _fetch()
    rec = result.records[0]
    # The 2020 row is first in the file; the newest must be chosen by date, not by position.
    assert rec.period == "2026-09-15", rec.period
    assert rec.value == 128753092.0
    assert rec.provenance["previousShortPositionQuantity"] == 139749097
    assert rec.provenance["daysToCoverQuantity"] == 3.0
    assert rec.posture.authority_level == "L2"
    assert rec.posture.source_id == "finra_short_interest_api"
    assert "128,753,092" in rec.claim_text
    assert "orderNote" in rec.provenance and "LAST row" in rec.provenance["orderNote"]
    print("ok the newest settlement is chosen by date rather than by row position")


def test_csv_is_parsed_and_the_body_is_posted_as_json() -> None:
    body = _csv(AAPL_ROWS)
    with mock.patch.object(fs.urllib.request, "urlopen") as opener:
        opener.return_value.__enter__.return_value.read.return_value = body.encode("utf-8")
        fs.fetch_short_interest("AAPL", as_of="2026-10-04")
    request = opener.call_args[0][0]
    assert request.data, "the filter must be sent as a POST body, not query parameters"
    import json
    payload = json.loads(request.data.decode("utf-8"))
    # GET parameters are ignored by this API, so filtering must live in the body.
    assert "compareFilters" in payload and "dateRangeFilters" in payload
    assert payload["compareFilters"][0]["fieldName"] == "symbolCode"
    assert payload["compareFilters"][0]["fieldValue"] == "AAPL"
    print("ok the CSV body parses and the filter travels as a POSTed JSON body")


def test_quoted_csv_and_quote_wrapped_numbers_survive() -> None:
    # The live file arrives fully quoted; a stray quote would corrupt a column name or a value.
    quoted = ('"accountingYearMonthNumber","symbolCode","issueName","marketClassCode",'
              '"currentShortPositionQuantity","settlementDate"\n'
              '"20260915","AAPL","Apple Inc.","NNM","128753092","2026-09-15"\n')
    with mock.patch.object(fs.urllib.request, "urlopen") as opener:
        opener.return_value.__enter__.return_value.read.return_value = quoted.encode("utf-8")
        rec = fs.fetch_short_interest("AAPL", as_of="2026-10-04").records[0]
    assert rec.value == 128753092.0, rec.value
    assert rec.period == "2026-09-15"
    print("ok quoted CSV parses without corrupting column names or values")


def test_symbol_mismatch_and_empty_window_are_labelled_gaps() -> None:
    # A batch that does not actually contain the requested symbol must not be published as it.
    other = [dict(AAPL_ROWS[-1], symbolCode="MSFT", issueName="Microsoft")]
    try:
        _fetch(other)
    except net.FetchError as exc:
        assert "finra_source_gap" in str(exc)
        assert "AAPL" in str(exc)
    else:
        raise AssertionError("another symbol's row must not be published under the requested one")
    # A window that misses the semi-monthly cycle is a gap with the reason, not an empty series.
    try:
        _fetch([])
    except net.FetchError as exc:
        message = str(exc)
        assert "semi-monthly" in message and "widen the window" in message
    else:
        raise AssertionError("an empty window must be a labelled gap")
    print("ok a symbol mismatch and an empty window are both labelled gaps")


def test_no_symbol_is_refused_with_the_reason() -> None:
    try:
        fs.fetch_short_interest("", as_of="2026-10-04")
    except net.FetchError as exc:
        assert "keyed by symbol" in str(exc)
    else:
        raise AssertionError("the file is keyed by symbol, so an empty one must be refused")
    print("ok a missing symbol is refused because the file is keyed by symbol")


MARKET_ROWS = [
    {"symbolCode": "GME", "issueName": "GameStop", "marketClassCode": "NNM",
     "currentShortPositionQuantity": 50000000, "previousShortPositionQuantity": 40000000,
     "daysToCoverQuantity": 9.1, "changePercent": 25.0, "settlementDate": "2026-09-15"},
    {"symbolCode": "AAPL", "issueName": "Apple Inc.", "marketClassCode": "NNM",
     "currentShortPositionQuantity": 128753092, "previousShortPositionQuantity": 139749097,
     "daysToCoverQuantity": 3.0, "changePercent": -7.87, "settlementDate": "2026-09-15"},
    {"symbolCode": "TINY", "issueName": "Tiny Co", "marketClassCode": "NNM",
     "currentShortPositionQuantity": 1000, "previousShortPositionQuantity": 900,
     "daysToCoverQuantity": 0.1, "changePercent": 11.0, "settlementDate": "2026-09-15"},
]


def test_market_sweep_ranks_by_short_position_and_flags_truncation() -> None:
    body = _csv(MARKET_ROWS)
    with mock.patch.object(fs.urllib.request, "urlopen") as opener:
        opener.return_value.__enter__.return_value.read.return_value = body.encode("utf-8")
        result = fs.fetch_short_interest_market("", as_of="2026-10-04", limit=2)
    # Sorted descending, so the claim leads with the largest rather than an arbitrary row.
    assert [r.provenance["symbol"] for r in result.records] == ["AAPL", "GME"]
    assert result.records[0].provenance["rankedBy"].startswith("currentShortPositionQuantity")
    assert all(r.period == "2026-09-15" for r in result.records)
    # An untruncated read must say so rather than implying a sample.
    assert "whole settlement batch" in result.records[0].provenance["truncationNote"]
    print("ok the market sweep ranks by short position and states whether it was truncated")


def main() -> int:
    test_newest_settlement_wins_and_not_the_first_row()
    test_csv_is_parsed_and_the_body_is_posted_as_json()
    test_quoted_csv_and_quote_wrapped_numbers_survive()
    test_symbol_mismatch_and_empty_window_are_labelled_gaps()
    test_no_symbol_is_refused_with_the_reason()
    test_market_sweep_ranks_by_short_position_and_flags_truncation()
    print("mira_data_finra_short_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
