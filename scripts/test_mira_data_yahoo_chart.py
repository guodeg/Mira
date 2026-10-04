#!/usr/bin/env python3
"""Offline tests for the Yahoo chart adapter (``market_price`` / technical bars).

This adapter had no offline coverage before, which is why a real defect sat in it: it recorded
the exchange name but **discarded the exchange timezone**, even though Yahoo returns it.
Mira's protocol resolves a relative date in the *instrument's* market timezone, so "today" for
`005930.KS` is an ``Asia/Seoul`` date rather than the user's or New York's. Without the timezone
on the claim, freshness cannot be re-checked without re-deriving it from the ticker suffix — an
assumption exactly of the kind that breaks silently.

The fixtures below reproduce the live v8 shape measured on 2026-10-04 across KR/HK/JP/DE/US/CN,
including the two things that differ per market: the timezone and the currency.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import net
from tools.mira_data.adapters import yahoo_chart as yc


def _payload(
    *,
    symbol="005930.KS",
    currency="KRW",
    tz="Asia/Seoul",
    offset=32400,
    exchange="KSC",
    full_exchange="Korea Stock Exchange",
    last_close=276000.0,
    bars=3,
):
    """One v8 chart response with N daily bars."""
    stamps, opens, highs, lows, closes, volumes = [], [], [], [], [], []
    for i in range(bars):
        stamps.append(1759100000 + i * 86400)
        opens.append(270000.0 + i)
        highs.append(280000.0 + i)
        lows.append(268000.0 + i)
        closes.append(last_close - (bars - 1 - i))
        volumes.append(1000000 + i)
    return {"chart": {"result": [{
        "meta": {
            "currency": currency, "symbol": symbol,
            "exchangeName": exchange, "fullExchangeName": full_exchange,
            "exchangeTimezoneName": tz, "gmtoffset": offset,
            "regularMarketPrice": last_close,
            "fiftyTwoWeekHigh": 300000.0, "fiftyTwoWeekLow": 200000.0,
            "regularMarketVolume": 1234567,
        },
        "timestamp": stamps,
        "indicators": {
            "quote": [{"open": opens, "high": highs, "low": lows, "close": closes,
                       "volume": volumes}],
            "adjclose": [{"adjclose": closes}],
        },
    }]}}

def _fetch(**kwargs):
    payload = kwargs.pop("payload", None) or _payload(**kwargs)
    with mock.patch.object(yc.net, "get_json", return_value=payload):
        return yc.fetch_market_price("005930.KS", as_of="2026-10-04")


def test_exchange_timezone_is_recorded_for_every_market() -> None:
    cases = [
        ("005930.KS", "Asia/Seoul", 32400, "KRW", "KSC"),
        ("0700.HK", "Asia/Hong_Kong", 28800, "HKD", "HKG"),
        ("7203.T", "Asia/Tokyo", 32400, "JPY", "JPX"),
        ("SAP.DE", "Europe/Berlin", 7200, "EUR", "GER"),
        ("AAPL", "America/New_York", -14400, "USD", "NMS"),
        ("000300.SS", "Asia/Shanghai", 28800, "CNY", "SHH"),
    ]
    for symbol, tz, offset, currency, code in cases:
        result = _fetch(payload=_payload(symbol=symbol, currency=currency, tz=tz,
                                         offset=offset, exchange=code))
        rec = result.records[0]
        prov = rec.provenance
        assert prov["exchangeTimezoneName"] == tz, (symbol, prov)
        assert prov["gmtoffsetSeconds"] == offset, (symbol, prov)
        assert prov["exchangeCode"] == code, (symbol, prov)
        # The note is the point: it tells a reader which field resolves a relative date.
        assert "exchangeTimezoneName" in prov["sessionNote"], symbol
    print("ok the exchange timezone and offset are recorded for every market")


def test_timezone_survives_a_missing_value_without_inventing_one() -> None:
    payload = _payload()
    del payload["chart"]["result"][0]["meta"]["exchangeTimezoneName"]
    del payload["chart"]["result"][0]["meta"]["gmtoffset"]
    result = _fetch(payload=payload)
    prov = result.records[0].provenance
    # An unrecorded timezone must stay absent rather than defaulting to the user's zone.
    assert prov["exchangeTimezoneName"] is None
    assert prov["gmtoffsetSeconds"] is None
    print("ok a missing timezone stays absent instead of being defaulted")


def test_currency_and_tier_are_unchanged() -> None:
    result = _fetch()
    rec = result.records[0]
    assert rec.claim_type == "market_pricing", rec.claim_type
    assert rec.posture.authority_level == "L5", "a relayed quote is not L2"
    assert rec.posture.source_id == "yahoo_chart_api_v8"
    assert rec.currency == "KRW" if hasattr(rec, "currency") else True
    assert "KRW" in rec.claim_text, rec.claim_text
    assert "276,000" in rec.claim_text or "276000" in rec.claim_text
    metrics = {r.metric for r in result.records}
    # The snapshot claims, and the history goes to the side series rather than the log.
    assert {"last_close", "fifty_two_week_high", "fifty_two_week_low", "last_volume"} <= metrics
    assert result.series and len(result.series["rows"]) == 3
    assert result.series["columns"] == yc.SERIES_COLUMNS
    print("ok currency, L5 tier, snapshot metrics and the side series are unchanged")


def test_error_and_empty_paths_are_labelled_gaps() -> None:
    with mock.patch.object(yc.net, "get_json",
                           return_value={"chart": {"error": {"code": "Not Found"}}}):
        try:
            yc.fetch_market_price("NOPE", as_of="2026-10-04")
        except net.FetchError as exc:
            assert "technical_source_gap" in str(exc) and "NOPE" in str(exc)
        else:
            raise AssertionError("a provider error must be a labelled gap")
    with mock.patch.object(yc.net, "get_json", return_value={"chart": {"result": []}}):
        try:
            yc.fetch_market_price("NOPE", as_of="2026-10-04")
        except net.FetchError as exc:
            assert "empty chart" in str(exc)
        else:
            raise AssertionError("an empty chart must be a labelled gap")
    payload = _payload(bars=0)
    with mock.patch.object(yc.net, "get_json", return_value=payload):
        try:
            yc.fetch_market_price("005930.KS", as_of="2026-10-04")
        except net.FetchError as exc:
            assert "no usable bars" in str(exc)
        else:
            raise AssertionError("a chart with no bars must be a labelled gap")
    print("ok provider error, empty chart and no-bars are all labelled gaps")


def main() -> int:
    test_exchange_timezone_is_recorded_for_every_market()
    test_timezone_survives_a_missing_value_without_inventing_one()
    test_currency_and_tier_are_unchanged()
    test_error_and_empty_paths_are_labelled_gaps()
    print("mira_data_yahoo_chart_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
