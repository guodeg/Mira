#!/usr/bin/env python3
"""Offline tests for the item-6 sources: Treasury Fiscal Data and CFTC COT (both L2, keyless).

The findings that shape these tests, all measured live 2026-10-03:

1. **The CFTC's default ordering is alphabetical, and a substring filter is not a unique key.**
   "GOLD" matches COMEX gold (open interest 406,456) *and* a Coinbase PAX-Gold perpetual
   (1,512), so the minor market was being surfaced as "the" gold position. Rows are now ordered
   by report date then open interest descending.
2. **The CFTC dataset labels were initially wrong.** `6dca-aqww` serves WHEAT-SRW — it is the
   legacy report covering commodity *and* financial markets, not a "financial futures" report.
   A test pins the labels against what each dataset actually returns.
3. **`avg_interest_rates` mixes instruments.** One "latest" row would silently pick whichever
   sorts first, so each security description is claimed separately.
4. **The CLI passes the symbol positionally**, so a pre-bound ``dataset=``/``market=`` kwarg
   collides with "multiple values for argument". The family entry points are named wrappers.
5. **Amounts arrive as strings** (including decimals) and are parsed explicitly.
"""

from __future__ import annotations

import sys
import urllib.parse
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import net
from tools.mira_data.adapters import cftc_cot, treasury_fiscal


DEBT_PAYLOAD = {"data": [
    {"record_date": "2026-10-01", "debt_held_public_amt": "32433790394253.86",
     "intragov_hold_amt": "7826851578136.17", "tot_pub_debt_out_amt": "40260641972390.03",
     "record_fiscal_year": "2027"},
], "meta": {"total-count": 1}}

AVG_PAYLOAD = {"data": [
    {"record_date": "2026-08-31", "security_type_desc": "Marketable",
     "security_desc": "Treasury Bills", "avg_interest_rate_amt": "3.788"},
    {"record_date": "2026-08-31", "security_type_desc": "Marketable",
     "security_desc": "Treasury Notes", "avg_interest_rate_amt": "3.345"},
    {"record_date": "2026-08-31", "security_type_desc": "Marketable",
     "security_desc": "Treasury Bonds", "avg_interest_rate_amt": "3.453"},
], "meta": {"total-count": 16}}

# Live shape: "GOLD" matches a major and a minor market; ordering must surface the major.
COT_ROWS = [
    {"market_and_exchange_names": "GOLD - COMMODITY EXCHANGE INC.",
     "report_date_as_yyyy_mm_dd": "2026-09-29T00:00:00.000",
     "open_interest_all": "406456", "noncomm_positions_long_all": "249736",
     "noncomm_positions_short_all": "31104", "noncomm_postions_spread_all": "49105",
     "comm_positions_long_all": "58831", "comm_positions_short_all": "309798",
     "nonrept_positions_long_all": "48784", "nonrept_positions_short_all": "16449"},
    {"market_and_exchange_names": "PAX GOLD PERP STYLE - COINBASE DERIVATIVES, LLC",
     "report_date_as_yyyy_mm_dd": "2026-09-29T00:00:00.000",
     "open_interest_all": "1512", "noncomm_positions_long_all": "782",
     "noncomm_positions_short_all": "1401", "noncomm_postions_spread_all": "0"},
]


def test_treasury_debt_parses_string_amounts() -> None:
    with mock.patch.object(treasury_fiscal.net, "get_json", return_value=DEBT_PAYLOAD):
        result = treasury_fiscal.fetch_treasury_series("debt", as_of="2026-10-04")
    rec = result.records[0]
    assert rec.value == 40260641972390.03, rec.value
    assert rec.unit == "usd" and rec.period == "2026-10-01"
    assert rec.posture.authority_level == "L2" and rec.posture.source_id == "treasury_fiscal_api"
    assert rec.provenance["debtHeldByPublic"] == 32433790394253.86
    assert rec.provenance["intragovernmentalHoldings"] == 7826851578136.17
    # The string-amount quirk is recorded, because a future reader will wonder why it matters.
    assert "STRING" in rec.provenance["amountBasis"]
    print("ok Treasury debt parses decimal strings and claims the official total")


def test_treasury_avg_interest_claims_each_instrument() -> None:
    with mock.patch.object(treasury_fiscal.net, "get_json", return_value=AVG_PAYLOAD):
        result = treasury_fiscal.fetch_treasury_series("avg_interest", as_of="2026-10-04")
    # One claim per security description, not one "latest" row.
    metrics = {r.metric for r in result.records}
    assert metrics == {"treasury_avg_interest_treasury_bills",
                       "treasury_avg_interest_treasury_notes",
                       "treasury_avg_interest_treasury_bonds"}, metrics
    assert all(r.unit == "percent" for r in result.records)
    assert all("mixes" in r.provenance["perInstrumentNote"] for r in result.records)
    print("ok average interest rates are claimed per instrument, not as one latest row")


def test_treasury_rejects_an_unwired_dataset() -> None:
    try:
        treasury_fiscal.fetch_treasury_series("exchange_rates", as_of="2026-10-04")
    except net.FetchError as exc:
        message = str(exc)
        assert "treasury_dataset_gap" in message
        # The refusal names the datasets that ARE wired and says why the other is not claimed.
        assert "debt" in message and "avg_interest" in message
        assert "network" in message
    else:
        raise AssertionError("an unwired dataset must be refused, not guessed at")
    print("ok an unwired Treasury dataset is refused with the datasets that are wired")


def test_cftc_orders_by_open_interest_so_the_major_market_leads() -> None:
    with mock.patch.object(cftc_cot.net, "get_json", return_value=COT_ROWS) as getter:
        result = cftc_cot.fetch_cot("GOLD", as_of="2026-10-04", weeks=8)
    url = getter.call_args[0][0]
    # The ordering is the fix: alphabetical would put the Coinbase perpetual first.
    assert "open_interest_all" in url and "DESC" in url, url
    decoded = urllib.parse.unquote_plus(url)
    assert "upper(market_and_exchange_names) like" in decoded, decoded
    # The market filter must be folded to upper case so it matches the report's own casing.
    assert "'%GOLD%'" in decoded, decoded
    first = result.records[0]
    assert first.value == 406456.0, "COMEX gold must lead, not the 1,512-lot perpetual"
    assert "GOLD - COMMODITY EXCHANGE INC." in first.claim_text
    assert first.posture.source_id == "cftc_cot_api" and first.posture.authority_level == "L2"
    # The minor market stays visible in the series rather than being dropped.
    markets = {r["market"] for r in result.series["rows"]}
    assert len(markets) == 2, markets
    print("ok CFTC rows are ordered by open interest so the major market leads")


def test_cftc_escaping_and_empty_result() -> None:
    with mock.patch.object(cftc_cot.net, "get_json", return_value=COT_ROWS) as getter:
        cftc_cot.fetch_cot("O'BRIEN", as_of="2026-10-04")
    url = getter.call_args[0][0]
    # A single quote in a market name must be doubled, not interpolated raw.
    assert "O''BRIEN" in url.replace("%27", "'"), url
    with mock.patch.object(cftc_cot.net, "get_json", return_value=[]):
        try:
            cftc_cot.fetch_cot("NOPE", as_of="2026-10-04")
        except net.FetchError as exc:
            assert "cftc_source_gap" in str(exc)
            assert "weekly" in str(exc), "the gap should say why a filter can legitimately be empty"
        else:
            raise AssertionError("an empty COT result must be a labelled gap")
    print("ok COT escapes quotes in the market filter and labels an empty result")


def test_cftc_report_labels_match_what_each_dataset_returns() -> None:
    # The labels are the correction: 6dca-aqww is the legacy report (it serves WHEAT-SRW),
    # NOT a financial-futures report. Claiming otherwise would misdescribe every position.
    assert cftc_cot.REPORTS["legacy"][0] == "6dca-aqww"
    assert "legacy" in cftc_cot.REPORTS["legacy"][1].lower()
    assert "commodity and financial" in cftc_cot.REPORTS["legacy"][1]
    assert cftc_cot.REPORTS["disaggregated"][0] == "72hh-3qpy"
    assert cftc_cot.REPORTS["tff"][0] == "gpe5-46if"
    with mock.patch.object(cftc_cot.net, "get_json", return_value=COT_ROWS):
        try:
            cftc_cot.fetch_cot("GOLD", report="financial", as_of="2026-10-04")
        except net.FetchError as exc:
            assert "cftc_report_gap" in str(exc)
            assert "legacy" in str(exc) and "tff" in str(exc)
        else:
            raise AssertionError("the old 'financial' label must no longer be accepted")
    print("ok the CFTC report labels say what each dataset actually returns")


def test_family_wrappers_accept_the_positional_symbol() -> None:
    # The CLI always passes the symbol positionally; a pre-bound kwarg raised
    # "multiple values for argument". The wrappers make the mapping explicit.
    with mock.patch.object(treasury_fiscal.net, "get_json", return_value=AVG_PAYLOAD):
        result = treasury_fiscal.fetch_avg_interest("", as_of="2026-10-04")
    assert result.records and all("avg_interest" in r.metric for r in result.records)
    with mock.patch.object(cftc_cot.net, "get_json", return_value=COT_ROWS):
        direct = cftc_cot.fetch_cot_family("GOLD", as_of="2026-10-04")
    assert direct.records[0].value == 406456.0
    print("ok the family wrappers take the positional symbol the CLI passes")


def main() -> int:
    test_treasury_debt_parses_string_amounts()
    test_treasury_avg_interest_claims_each_instrument()
    test_treasury_rejects_an_unwired_dataset()
    test_cftc_orders_by_open_interest_so_the_major_market_leads()
    test_cftc_escaping_and_empty_result()
    test_cftc_report_labels_match_what_each_dataset_returns()
    test_family_wrappers_accept_the_positional_symbol()
    print("mira_data_us_official_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
