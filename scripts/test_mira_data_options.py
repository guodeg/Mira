#!/usr/bin/env python3
"""Regression tests for the A-share ETF options-surface adapter.

Offline: the CLI runner is patched, so no CLI, no credentials, no network. The tests pin
the parts that are easy to get subtly wrong — client-side selection from a vendor list
that has no per-underlying filter, nearest-expiry choice, the authoritative strike coming
from contract-detail rather than a parsed ticker, per-contract quote failures degrading
per contract instead of failing the whole surface, and the honest record that open
interest and implied volatility are simply not published.
"""

from __future__ import annotations

import csv
import sys
import tempfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import net
from tools.mira_data.adapters import hithink_options as opt
from tools.mira_data.emit import emit_bundle


def _contract(thscode, ticker, name, last_trade="2026-12-23", end="2026-12-24"):
    return {"thscode": thscode, "ticker": ticker, "name": name, "exchange_code": "SSE",
            "variety_code": "510050O", "list_date": "2026-04-23", "end_date": end,
            "last_trade_date": last_trade, "last_delivery_date": last_trade}


CONTRACTS = {"item": [
    _contract("10011425.SH", "510050C2612M02800", "50ETF购12月2800"),
    _contract("10011426.SH", "510050P2612M02800", "50ETF沽12月2800"),
    _contract("10011500.SH", "510050C2703M03000", "50ETF购3月3000",
              last_trade="2027-03-24", end="2027-03-25"),
    _contract("10020001.SH", "510300C2612M04000", "300ETF购12月4000"),
]}

DETAIL = {
    "10011425.SH": {"strike_price": 2.8, "option_type": "call", "exercise_style": "european",
                    "underlying_code": "510050", "margin_rate": 12, "trade_amount": 10000,
                    "last_trade_date": "2026-12-23"},
    "10011426.SH": {"strike_price": 2.8, "option_type": "put", "exercise_style": "european",
                    "underlying_code": "510050", "margin_rate": 12, "trade_amount": 10000,
                    "last_trade_date": "2026-12-23"},
    "10011500.SH": {"strike_price": 3.0, "option_type": "call", "exercise_style": "european",
                    "underlying_code": "510050", "margin_rate": 12, "trade_amount": 10000,
                    "last_trade_date": "2027-03-24"},
}
# 2026-09-14 and 2026-09-15 at Beijing midnight.
BARS = {"10011425.SH": [{"timestamp": 1789315200000, "close_price": 0.1688, "volume": 200},
                        {"timestamp": 1789401600000, "close_price": 0.1755, "volume": 268,
                         "turnover": 456129}],
        "10011426.SH": [{"timestamp": 1789401600000, "close_price": 0.0912, "volume": 310}],
        "10011500.SH": [{"timestamp": 1789401600000, "close_price": 0.2210, "volume": 40}]}


def _runner(calls: list | None = None, *, bars=None):
    bars = BARS if bars is None else bars

    def run(args, timeout=None):
        if calls is not None:
            calls.append(list(args))
        if args[:2] == ["options", "contracts"]:
            return CONTRACTS
        if args[:2] == ["options", "contract-detail"]:
            code = args[args.index("--thscode") + 1]
            return DETAIL.get(code, {})
        if args[:2] == ["options", "daily"]:
            code = args[args.index("--thscode") + 1]
            return {"item": bars.get(code, [])}
        raise AssertionError(f"unexpected call: {args}")

    return run


def test_surface_filters_by_underlying_and_picks_nearest_expiry() -> None:
    with mock.patch.object(opt, "hithink_run", side_effect=_runner()):
        result = opt.fetch_option_surface("510050", as_of="2026-10-02")
    tickers = sorted(r.provenance["ticker"] for r in result.records)
    assert tickers == ["510050C2612M02800", "510050P2612M02800"], tickers
    assert all("510300" not in r.provenance["ticker"] for r in result.records)

    call = next(r for r in result.records if r.provenance["optionType"] == "call")
    assert call.family == "options_surface"
    assert call.metric == "option_last_close" and call.unit == "CNY"
    assert call.value == 0.1755
    assert call.research_object == "510050.SH"
    assert call.period == "2026-09-15" and call.source_date == "2026-09-15"
    assert call.posture.source_id == "hithink_finance_api"
    assert call.posture.claim_type == "market_pricing"
    assert call.posture.authority_level == "L5"
    # Strike and type come from contract-detail, not from parsing the ticker.
    assert call.provenance["strikePrice"] == 2.8
    assert call.provenance["contractSize"] == 10000
    assert call.provenance["expiry"] == "2026-12-23"
    assert call.provenance["daysToExpiry"] == 99
    assert call.provenance["notPublished"] == "open_interest,implied_volatility"
    print("ok the surface filters one underlying, picks the nearest expiry and prices it")


def test_explicit_expiry_normalisation_and_cap() -> None:
    with mock.patch.object(opt, "hithink_run", side_effect=_runner()):
        result = opt.fetch_option_surface("510050", as_of="2026-10-02", expiry="2703")
    assert [r.provenance["ticker"] for r in result.records] == ["510050C2703M03000"]

    with mock.patch.object(opt, "hithink_run", side_effect=_runner()):
        result = opt.fetch_option_surface("510050", as_of="2026-10-02", max_contracts=1)
    assert len(result.records) == 1

    with mock.patch.object(opt, "hithink_run", side_effect=_runner()):
        try:
            opt.fetch_option_surface("510050", as_of="2026-10-02", expiry="2028-01")
        except net.FetchError as exc:
            assert "options_source_gap" in str(exc) and "available" in str(exc)
        else:
            raise AssertionError("an unlisted expiry must be rejected with the options")
    print("ok expiry shorthands normalise, the contract cap holds, and bad expiries list options")


def test_ticker_expiry_parse_and_bad_input() -> None:
    assert opt._expiry_from_ticker("510050C2612M02800") == "2026-12"
    assert opt._expiry_from_ticker("510050P2703A03000") == "2027-03"
    assert opt._expiry_from_ticker("510050XXXX") == ""
    assert opt._normalise_expiry("202612") == "2026-12"
    assert opt._normalise_expiry("2026-12") == "2026-12"
    try:
        opt._normalise_expiry("December")
    except net.FetchError as exc:
        assert "invalid_expiry" in str(exc)
    else:
        raise AssertionError("an unparsable expiry must be rejected")
    print("ok ticker expiry parsing is used for grouping and bad input is rejected")


def test_a_failed_quote_skips_only_that_contract() -> None:
    partial = dict(BARS)
    partial["10011426.SH"] = []          # one contract returns no bars
    with mock.patch.object(opt, "hithink_run", side_effect=_runner(bars=partial)):
        result = opt.fetch_option_surface("510050", as_of="2026-10-02")
    assert [r.provenance["ticker"] for r in result.records] == ["510050C2612M02800"]

    with mock.patch.object(opt, "hithink_run", side_effect=_runner(bars={})):
        try:
            opt.fetch_option_surface("510050", as_of="2026-10-02")
        except net.FetchError as exc:
            assert "options_source_gap" in str(exc)
        else:
            raise AssertionError("a surface with no priced contract is a source gap")
    print("ok one unpriced contract degrades alone; none priced is a source gap")


def test_bj_names_degrade_without_calls() -> None:
    calls: list = []
    with mock.patch.object(opt, "hithink_run", side_effect=_runner(calls)):
        try:
            opt.fetch_option_surface("830799", as_of="2026-10-02")
        except net.FetchError as exc:
            assert "options_source_gap" in str(exc)
        else:
            raise AssertionError("expected a gap for a Beijing-exchange name")
    assert calls == []
    print("ok Beijing-exchange names degrade without spending a request")


def test_emitted_rows_are_market_pricing_with_the_data_limit_recorded() -> None:
    with mock.patch.object(opt, "hithink_run", side_effect=_runner()):
        result = opt.fetch_option_surface("510050", as_of="2026-10-02")
    scratch = ROOT / "local"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as out:
        emitted = emit_bundle(result.records, out_dir=out, research_object="510050.SH",
                              market_scope="CN", endpoint=opt.ENDPOINT.format(thscode="510050.SH"))
        assert emitted["calculation_ledger"] is None
        with open(Path(out) / "evidence-log.csv", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    assert {row["claim_type"] for row in rows} == {"market_pricing"}
    assert {row["verification_status"] for row in rows} == {"verified"}
    assert {row["authority_level"] for row in rows} == {"L5"}
    assert {row["source_id"] for row in rows} == {"hithink_finance_api"}
    assert all("notPublished=open_interest,implied_volatility" in row["notes"] for row in rows)
    print("ok emitted option rows are L5 market pricing that state what is not published")


def main() -> int:
    test_surface_filters_by_underlying_and_picks_nearest_expiry()
    test_explicit_expiry_normalisation_and_cap()
    test_ticker_expiry_parse_and_bad_input()
    test_a_failed_quote_skips_only_that_contract()
    test_bj_names_degrade_without_calls()
    test_emitted_rows_are_market_pricing_with_the_data_limit_recorded()
    print("mira_data_options_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
