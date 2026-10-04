#!/usr/bin/env python3
"""Offline tests for benchmark routing in ``technical``.

The default benchmark was ``SPY`` unconditionally, so every A-share was measured against the wrong
market. The tests pin the three things that make the fix correct rather than merely present:

1. **Priority.** An explicit ``--benchmark`` always wins; only an absent one triggers routing.
2. **Bare index codes must resolve to the INDEX.** This is the subtle one. The generic A-share rule
   is "0xxxxx -> .SZ", which is right for Shenzhen *equities* and wrong for index codes — and the
   failure is not a 404. Probing Yahoo showed bare ``000905`` resolving to **"XPD"**, ``000016`` to
   **"KONKA GROUP"** and ``000852`` to **"SOFE"**: real Shenzhen stocks sharing the number. A
   benchmark silently swapped for an unrelated small-cap would corrupt every relative-strength
   figure while looking healthy, so index codes resolve through their own map first.
3. **The resolved benchmark is reported, not just used** — including whether it was automatic,
   because an auto-chosen baseline is the easiest thing to mistake for the old SPY default.

Also pinned: A-share indices read from their **official** publisher, because Yahoo serves exactly
ONE bar for ``000300.SS`` on every range, which makes relative strength silently ``None``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import benchmarks as bm


def test_explicit_benchmark_always_wins() -> None:
    # Even for an A-share, an explicit choice is honoured rather than overridden by routing.
    choice = bm.resolve_benchmark("600183", "SPY")
    assert choice.symbol == "SPY" and choice.automatic is False
    assert choice.annotation == "SPY [explicit]"
    # And it is still normalised.
    assert bm.resolve_benchmark("600183", "000300").symbol == "000300.SS"
    assert bm.resolve_benchmark("AAPL", "^GSPC").symbol == "^GSPC"
    print("ok an explicit benchmark always wins and is normalised")


def test_absent_benchmark_routes_by_market() -> None:
    cases = {
        "600183": "000300.SS",      # bare A-share code
        "600183.SS": "000300.SS",   # already suffixed
        "000001": "000300.SS",      # Shenzhen
        "300750": "000300.SS",   # ChiNext still gets the market baseline
        "430047": "000300.SS",      # Beijing
        "0700.HK": "^HSI",          # Hong Kong
        "AAPL": "SPY",              # US
        "^GSPC": "SPY",
    }
    for ticker, expected in cases.items():
        choice = bm.resolve_benchmark(ticker, None)
        assert choice.symbol == expected, (ticker, choice.symbol, expected)
        assert choice.automatic is True, ticker
        assert "[auto]" in choice.annotation
    # An empty string is "not supplied", which is what an unset CLI flag yields.
    assert bm.resolve_benchmark("600183", "").symbol == "000300.SS"
    assert bm.resolve_benchmark("600183", "   ").automatic is True
    print("ok an absent benchmark routes by the instrument's market")


def test_bare_index_codes_resolve_to_the_index_not_a_same_numbered_stock() -> None:
    # The map exists because 0xxxxx -> .SZ is right for equities and wrong for index codes, and the
    # wrong answer is a REAL security rather than an error.
    expected = {
        "000300": "000300.SS", "000905": "000905.SS", "000016": "000016.SS",
        "000852": "000852.SS", "000688": "000688.SS", "000906": "000906.SS",
        "399001": "399001.SZ", "399006": "399006.SZ",
    }
    for code, want in expected.items():
        assert bm.resolve_index(code) == want, (code, bm.resolve_index(code))
        # And the same code supplied explicitly as a benchmark takes the index route too.
        assert bm.resolve_benchmark("600183", code).symbol == want, code
    # The generic equity rule must still govern real Shenzhen equities, so routing has not
    # swallowed the prefix rule.
    from tools.mira_data import cn_symbols
    assert cn_symbols.to_yahoo_symbol("002415") == "002415.SZ"
    assert cn_symbols.to_yahoo_symbol("600183") == "600183.SS"
    # 000001 is genuinely ambiguous (SSE Composite vs 平安银行) and is mapped to the INDEX here,
    # because this module resolves benchmarks and the index is the plausible intent.
    assert bm.resolve_index("000001") == "000001.SS"
    print("ok bare index codes resolve to the index, and equity codes still use the equity rule")


def test_bare_index_code_reverse_lookup() -> None:
    assert bm.bare_index_code("000300.SS") == "000300"
    assert bm.bare_index_code("000300") == "000300"
    assert bm.bare_index_code("399006.SZ") == "399006"
    # A non-index symbol must not be routed to the official index channel.
    assert bm.bare_index_code("AAPL") is None
    assert bm.bare_index_code("600183.SS") is None
    assert bm.bare_index_code("SPY") is None
    print("ok the reverse lookup only claims real index symbols")


def test_a_share_indices_read_from_the_official_publisher() -> None:
    """Yahoo serves ONE bar for 000300.SS, which silently zeroes relative strength.

    The official channel returns 727 rows, so benchmarks in the index map are read from there and
    everything else still goes to the price adapter.
    """
    from tools.mira_data import technical as T

    sentinel = object()
    with mock.patch.object(T, "_official_index_series", return_value=sentinel) as official, \
         mock.patch.object(T, "_default_market_provider", return_value="yahoo"), \
         mock.patch.object(T.yahoo_chart, "fetch_market_price") as yahoo:
        got = T._fetch_technical_price("000300.SS", for_benchmark=True)
        assert got is sentinel, "an index benchmark must use the official series"
        official.assert_called_once_with("000300.SS")
        yahoo.assert_not_called()

    # A US benchmark has no official route and still goes to the price adapter.
    with mock.patch.object(T, "_default_market_provider", return_value="yahoo"), \
         mock.patch.object(T.yahoo_chart, "fetch_market_price") as yahoo:
        T._fetch_technical_price("SPY", for_benchmark=True)
        yahoo.assert_called_once()
    # And a non-benchmark fetch is never diverted to the index channel.
    with mock.patch.object(T, "_official_index_series") as official, \
         mock.patch.object(T, "_default_market_provider", return_value="yahoo"), \
         mock.patch.object(T.yahoo_chart, "fetch_market_price"):
        T._fetch_technical_price("600183")
        official.assert_not_called()
    print("ok A-share index benchmarks use the official series, everything else does not")


def test_market_of_classification() -> None:
    assert bm.market_of("600183") == "CN"
    assert bm.market_of("600183.SS") == "CN"
    assert bm.market_of("000001.SZ") == "CN"
    assert bm.market_of("0700.HK") == "HK"
    assert bm.market_of("AAPL") == "US"
    assert bm.market_of("7203.T") == "US", "an unmapped market falls back to the US baseline"
    assert bm.market_of("") == "US"
    print("ok market classification covers CN, HK and the fallback")


def test_support_is_below_the_close_and_resistance_above() -> None:
    """Levels must be on the correct side of the price.

    Found while comparing peers: for a confirmed downtrend (300476: close 210.30, resistance
    252.00) the exported ``support`` string listed 228.06, 236.96 AND 286.09 — three levels
    overhead, including the invalidation itself. The filtered list already existed two lines below
    in the same function (``lower_supports``); the exported field simply ignored it, so a reader
    could not tell which side of the market they were on.
    """
    from tools.mira_data import technical as T
    # A synthetic downtrend: price below every moving average, swing low just beneath it.
    highs = [300.0] * 20
    lows = [205.0] * 20
    close = 210.0
    levels = T._levels(highs, lows, close, ma20=240.0, ma50=260.0, ma200=286.0)
    support = [float(x) for x in levels["support"].split(";") if x != "source_gap"]
    resistance = [float(x) for x in levels["resistance"].split(";") if x != "source_gap"]
    assert support and all(v < close for v in support), (close, support)
    assert resistance and all(v >= close for v in resistance), (close, resistance)
    # 205 is the only level below 210, so it is both the nearest and the only support.
    assert support == [205.0], support
    assert levels["nearest_support"] == 205.0
    # The invalidation (ma200) is overhead here, so it belongs to resistance, not support.
    assert 286.0 in resistance and 286.0 not in support

    # An uptrend must still put the averages below the price, so the fix is not one-directional.
    levels_up = T._levels([200.0] * 20, [150.0] * 20, close=190.0,
                          ma20=180.0, ma50=170.0, ma200=150.0)
    support_up = [float(x) for x in levels_up["support"].split(";") if x != "source_gap"]
    resistance_up = [float(x) for x in levels_up["resistance"].split(";") if x != "source_gap"]
    assert all(v < 190.0 for v in support_up), support_up
    assert all(v >= 190.0 for v in resistance_up), resistance_up
    assert 200.0 in resistance_up, resistance_up
    print("ok support sits below the close and resistance above, in both directions")


def main() -> int:
    test_explicit_benchmark_always_wins()
    test_absent_benchmark_routes_by_market()
    test_bare_index_codes_resolve_to_the_index_not_a_same_numbered_stock()
    test_bare_index_code_reverse_lookup()
    test_a_share_indices_read_from_the_official_publisher()
    test_market_of_classification()
    test_support_is_below_the_close_and_resistance_above()
    print("mira_data_benchmark_routing_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
