#!/usr/bin/env python3
"""Offline tests for A-share code normalisation.

``market_price`` and ``technical`` take a Yahoo ticker, so a bare A-share code used to fail with an
HTTP 404 that named only the URL — not the missing exchange suffix. Since the A-share workflow
starts from a six-digit code, that was a usability cliff in the middle of the localised stack. The
suffix is mechanically derivable, so it is derived rather than left to the caller.

The risk in a helper like this is the opposite failure: rewriting something it does not fully
understand. So the tests pin both directions — codes gain the right suffix, and everything else
(US tickers, HK/JP tickers, index symbols, already-suffixed codes) passes through untouched.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import cn_symbols as cs


def test_exchange_suffixes_are_derived_by_prefix() -> None:
    cases = {
        "600183": "600183.SS",   # Shanghai main board
        "688008": "688008.SS",   # STAR
        "900901": "900901.SS",   # Shanghai B-share
        "000001": "000001.SZ",   # Shenzhen main board
        "002415": "002415.SZ",   # Shenzhen SME
        "300750": "300750.SZ",   # ChiNext
        "200002": "200002.SZ",   # Shenzhen B-share
        "430047": "430047.BJ",   # Beijing
        "830799": "830799.BJ",
    }
    for code, expected in cases.items():
        assert cs.to_yahoo_symbol(code) == expected, (code, cs.to_yahoo_symbol(code))
        assert cs.is_bare_a_share(code), code
    print(f"ok {len(cases)} A-share prefixes map to the right exchange suffix")


def test_normalisation_is_idempotent() -> None:
    for code in ("600183.SS", "000001.SZ", "430047.BJ"):
        assert cs.to_yahoo_symbol(code) == code, code
        # A suffixed ticker is no longer "bare", so a second pass must not append again.
        assert not cs.is_bare_a_share(code), code
        assert cs.to_yahoo_symbol(cs.to_yahoo_symbol(code)) == code
    print("ok normalisation is idempotent and does not double-append a suffix")


def test_non_a_share_symbols_pass_through_untouched() -> None:
    # The dangerous failure is rewriting something we do not understand.
    untouched = ["AAPL", "MSFT", "^GSPC", "^VIX", "^HSI", "0700.HK", "7203.T", "2330.TW",
                 "005930.KS", "SAP.DE", "ASML.AS", "MC.PA", "BRK-B", "", "  ", "not-a-ticker",
                 "60018", "6001831"]
    for symbol in untouched:
        assert cs.to_yahoo_symbol(symbol) == symbol.strip() or symbol.strip() == "", (
            symbol, cs.to_yahoo_symbol(symbol))
        assert not cs.is_bare_a_share(symbol), symbol
    # Whitespace is trimmed, which is a normalisation and not a rewrite.
    assert cs.to_yahoo_symbol("  600183  ") == "600183.SS"
    print("ok US, HK, JP, TW, KR, EU and index symbols pass through untouched")


def test_exchange_lookup() -> None:
    assert cs.exchange_of("600183") == "SH"
    assert cs.exchange_of("000001") == "SZ"
    assert cs.exchange_of("430047") == "BJ"
    assert cs.exchange_of("600183.SS") == "SH"
    assert cs.exchange_of("000001.SZ") == "SZ"
    assert cs.exchange_of("AAPL") is None
    assert cs.exchange_of("0700.HK") is None
    assert cs.exchange_of("") is None
    print("ok exchange lookup handles bare codes, suffixed tickers and non-CN symbols")


def test_the_adapter_applies_it_so_callers_benefit() -> None:
    # The behaviour that matters is at the adapter boundary, not in this helper: a bare code must
    # reach Yahoo suffixed, so every caller (CLI, screening, tests) is fixed at once.
    from tools.mira_data.adapters import yahoo_chart as yc
    captured = {}

    def fake_get_json(url, **kwargs):
        captured["url"] = url
        raise yc.net.FetchError("stop here: the URL is what this test asserts")

    original = yc.net.get_json
    yc.net.get_json = fake_get_json
    try:
        try:
            yc.fetch_market_price("600183", as_of="2026-10-04")
        except yc.net.FetchError:
            pass
    finally:
        yc.net.get_json = original
    assert "/chart/600183.SS?" in captured["url"], captured["url"]
    print("ok the market-price adapter normalises a bare code before requesting")


def main() -> int:
    test_exchange_suffixes_are_derived_by_prefix()
    test_normalisation_is_idempotent()
    test_non_a_share_symbols_pass_through_untouched()
    test_exchange_lookup()
    test_the_adapter_applies_it_so_callers_benefit()
    print("mira_data_cn_symbols_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
