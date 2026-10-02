#!/usr/bin/env python3
"""Regression tests for the hithink-finance (Tonghuashun) A-share adapter.

The tests patch the CLI runner, so they need neither the ``hithink-finance`` CLI,
credentials, entitlements nor network access. They also pin the two contract
points that are easy to break silently: the vendor field map (``operating_income``
is revenue, ``total_debt`` is total liabilities) and registry membership of the
posture source ids.
"""

from __future__ import annotations

import csv
import datetime as _dt
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import __main__ as cli
from tools.mira_data import config, net
from tools.mira_data.adapters import hithink_finance as hf


SNAPSHOT = {
    "item": [{
        "thscode": "600519.SH", "ticker": "600519", "volume": 3833098,
        "turnover": 4797246600, "last_price": 1258.62, "price_change": 23.04,
        "price_change_ratio_pct": 1.864711, "open_price": 1239.53,
        "high_price": 1268, "low_price": 1236.05, "prev_price": 1235.58,
    }],
    "timestamp": 1790915611000,
    "total": 1,
}

INCOME = {
    "item": [
        {  # deliberately oldest-first: the adapter must not trust row order
            "thscode": "600519.SH", "period": "annual", "fiscal_year": 2024,
            "fiscal_period": "FY", "period_end_ms": hf._to_ms(_dt.date(2024, 12, 31)),
            "currency": "CNY", "operating_income": 100.0, "operating_profit": 60.0,
        },
        {
            "thscode": "600519.SH", "period": "annual", "fiscal_year": 2025,
            "fiscal_period": "FY", "currency": "CNY",
            # Raw vendor epochs exactly as returned for FY2025 (Beijing midnight).
            "report_date_ms": 1776355200000, "period_end_ms": 1767110400000,
            "operating_income": 168838102514.79, "operating_costs": 14892277570.91,
            "operating_profit": 114808950164.24, "net_profit": 85310324833.67,
            "parent_holder_net_profit": 82320067101.68,
            "research_and_development_expenses": 190112246.58, "basic_eps": 65.66,
        },
    ],
    "timestamp": hf._to_ms(_dt.date(2025, 12, 31)),
}

BALANCE = {
    "item": [
        {
            "thscode": "600519.SH", "period": "annual", "fiscal_year": 2024,
            "fiscal_period": "FY", "currency": "CNY",
            "assets_total": 1.0, "total_debt": 0.4, "holder_equity_total": 0.6, "cash": 0.1,
        },
        {
            "thscode": "600519.SH", "period": "annual", "fiscal_year": 2025,
            "fiscal_period": "FY", "currency": "CNY",
            "report_date_ms": 1786723200000, "period_end_ms": 1767110400000,
            "assets_total": 303834844021.44, "total_debt": 49875590112.37,
            "holder_equity_total": 253959253909.07, "cash": 51690610946.5,
        },
    ],
}

CASH_FLOW = {
    "item": [{
        "thscode": "600519.SH", "period": "annual", "fiscal_year": 2025,
        "fiscal_period": "FY", "currency": "CNY",
        "report_date_ms": 1776355200000, "period_end_ms": 1767110400000,
        "act_cash_flow_net": 61522204989.35,
        "pay_fixed_assets_etc_cash": 3127594916.41,
    }],
}


def _history_rows(count: int) -> dict:
    start = _dt.date(2024, 1, 1)
    rows = []
    for i in range(count):
        day = start + _dt.timedelta(days=i)
        rows.append({
            "date_ms": hf._to_ms(day),
            "volume": 1000 + i,
            "open_price": 10.0 + i,
            "high_price": 100.0 + i,
            "low_price": 1.0 + i,
            "close_price": 50.0 + i,
        })
    return {"item": rows, "thscode": "600519.SH", "interval": "1d", "adjust": "forward"}


def _runner(payloads: dict, calls: list | None = None):
    def run(args, timeout=None):
        if calls is not None:
            calls.append(list(args))
        for needle, payload in payloads.items():
            if needle in args:
                return payload
        raise AssertionError(f"unexpected CLI call: {args}")

    return run


def test_resolve_thscode_infers_board() -> None:
    assert hf.resolve_thscode("600519") == "600519.SH"
    assert hf.resolve_thscode("000001") == "000001.SZ"
    assert hf.resolve_thscode("300750") == "300750.SZ"
    assert hf.resolve_thscode("830799") == "830799.BJ"
    assert hf.resolve_thscode("600519.SH") == "600519.SH"
    assert hf.resolve_thscode("sh.600519") == "600519.SH"
    # Listed funds/ETFs: Shanghai 5xxxxx, Shenzhen 15xxxx/16xxxx/18xxxx.
    assert hf.resolve_thscode("510050") == "510050.SH"
    assert hf.resolve_thscode("588000") == "588000.SH"
    assert hf.resolve_thscode("159915") == "159915.SZ"
    assert hf.resolve_thscode("160105") == "160105.SZ"
    assert hf.resolve_thscode("sz.159915") == "159915.SZ"
    for bad in ("AAPL", "60051", "", "600519.US", "5100500"):
        try:
            hf.resolve_thscode(bad)
        except net.FetchError as exc:
            assert "invalid_symbol" in str(exc)
        else:
            raise AssertionError(f"expected invalid_symbol for {bad!r}")
    print("ok thscode resolution infers SH/SZ/BJ for stocks and listed funds/ETFs")


def test_market_price_maps_snapshot_and_bars() -> None:
    history = _history_rows(260)
    payloads = {"snapshot": SNAPSHOT, "history": history}
    with mock.patch.object(hf, "_run", side_effect=_runner(payloads)):
        result = hf.fetch_market_price("600519", as_of="2026-10-02")

    metrics = {record.metric: record for record in result.records}
    assert metrics["last_close"].value == 1258.62
    assert metrics["last_close"].unit == "CNY"
    assert metrics["last_volume"].value == 3833098
    assert metrics["last_volume"].unit == "shares"
    assert metrics["last_close"].posture.source_id == "hithink_finance_api"
    assert metrics["last_close"].posture.claim_type == "market_pricing"
    assert metrics["last_close"].research_object == "600519.SH"
    assert metrics["last_close"].market_scope == "CN"

    # 52-week window keeps only the last 252 of 260 bars.
    window = history["item"][-hf.TRADING_DAYS_52W:]
    assert metrics["fifty_two_week_high"].value == window[-1]["high_price"]
    assert metrics["fifty_two_week_low"].value == window[0]["low_price"]

    assert result.series["columns"] == hf.SERIES_COLUMNS
    assert len(result.series["rows"]) == 260
    assert result.series["rows"][0]["date"] == "2024-01-01"
    print("ok A-share snapshot + bars map to canonical market_price records")


def test_statement_field_map_and_derived_ledger() -> None:
    calls: list[list[str]] = []
    payloads = {"income": INCOME, "balance-sheet": BALANCE, "cash-flow": CASH_FLOW}
    with mock.patch.object(hf, "_run", side_effect=_runner(payloads, calls)):
        result = hf.fetch_company_financials("600519.SH", as_of="2026-10-02")

    metrics = {record.metric: record for record in result.records}
    # The two vendor-label traps: operating_income is revenue, total_debt is liabilities.
    assert metrics["revenue"].value == 168838102514.79
    assert metrics["revenue"].provenance["vendor_field"] == "operating_income"
    assert metrics["operating_income"].value == 114808950164.24
    assert metrics["operating_income"].provenance["vendor_field"] == "operating_profit"
    assert metrics["total_liabilities"].value == 49875590112.37
    assert metrics["total_liabilities"].provenance["vendor_field"] == "total_debt"
    assert metrics["stockholders_equity"].value == 253959253909.07

    # Latest fiscal year wins even though the vendor returned rows oldest-first.
    assert metrics["revenue"].period == "FY2025"
    assert metrics["revenue"].source_date == "2026-04-17"
    assert metrics["revenue"].provenance["period_end"] == "2025-12-31"
    assert metrics["basic_eps"].unit == "CNY/share"
    assert metrics["basic_eps"].currency is None

    gross = metrics["gross_profit"]
    assert abs(gross.value - (168838102514.79 - 14892277570.91)) < 1e-6
    assert gross.derived is True
    assert gross.posture.claim_type == "derived_calculation"
    assert gross.posture.authority_level == "L6"
    assert gross.upstream_sources == "hithink_finance_financials_api:600519.SH"
    assert "operating_costs" in gross.formula

    for call in calls:
        assert "--format" in call and "json" in call
        assert "--thscode" in call and "600519.SH" in call
    print("ok statements map to canonical metrics and gross_profit carries a ledger trail")


def test_cli_error_envelope_is_fetch_error() -> None:
    envelope = (
        '{"ok":false,"command":"market.snapshot",'
        '"error":{"code":"CLI_BAD_ARGUMENT","category":"validation",'
        '"message":"unknown option","hint":"see --help","retryable":false}}'
    )
    completed = subprocess.CompletedProcess(args=["hithink-finance"], returncode=1,
                                            stdout=envelope, stderr="")
    with (
        mock.patch.object(hf, "resolve_bin", return_value="/usr/local/bin/hithink-finance"),
        mock.patch.object(hf.subprocess, "run", return_value=completed),
    ):
        try:
            hf._run(["market", "snapshot", "--format", "json"])
        except net.FetchError as exc:
            assert "hithink_error" in str(exc) and "CLI_BAD_ARGUMENT" in str(exc)
        else:
            raise AssertionError("expected FetchError for an ok=false envelope")
    print("ok ok=false envelopes become source gaps with code, message and hint")


def test_non_json_output_is_source_gap() -> None:
    completed = subprocess.CompletedProcess(args=["hithink-finance"], returncode=0,
                                            stdout="update available: 0.1.14", stderr="")
    with (
        mock.patch.object(hf, "resolve_bin", return_value="/usr/local/bin/hithink-finance"),
        mock.patch.object(hf.subprocess, "run", return_value=completed),
    ):
        try:
            hf._run(["version", "--format", "json"])
        except net.FetchError as exc:
            assert "hithink_bad_output" in str(exc)
        else:
            raise AssertionError("expected FetchError for non-JSON output")
    print("ok non-JSON CLI output is reported as a source gap")


def test_calendar_epochs_are_beijing_midnight() -> None:
    """Vendor date epochs are Asia/Shanghai midnight, not UTC midnight.

    Decoding them with UTC moves every trading date, period end and filing date
    one calendar day earlier - a silent off-by-one in as-of dates. Every real
    vendor date field observed satisfies ``ms % 86_400_000 == 57_600_000``.
    """
    for raw in (1765123200000, 1767110400000, 1776355200000, 1786723200000):
        assert raw % 86_400_000 == 57_600_000, "sample no longer matches the vendor encoding"

    assert hf._ms_to_date(1767110400000) == "2025-12-31"   # FY2025 period end
    assert hf._ms_to_date(1776355200000) == "2026-04-17"   # annual report date
    assert hf._ms_to_date(1765123200000) == "2025-12-08"   # daily bar
    assert hf._ms_to_date(None) == ""

    day = _dt.date(2025, 12, 31)
    assert hf._ms_to_date(hf._to_ms(day)) == day.isoformat()  # encode/decode round-trip
    print("ok vendor calendar epochs decode in Asia/Shanghai without an off-by-one day")


def test_registry_rows_and_routing() -> None:
    registry = ROOT / "data" / "source-registry.csv"
    with registry.open(encoding="utf-8", newline="") as handle:
        known = {row["source_id"] for row in csv.DictReader(handle)}
    for key in ("hithink_finance_market", "hithink_finance_financials"):
        from tools.mira_data.canonical import POSTURES

        assert POSTURES[key].source_id in known, f"{key} posture is not registered"

    assert cli._default_market_scope("hithink_market_price") == "CN"
    assert cli._default_market_scope("market_price") == "US"
    with mock.patch.dict(os.environ, {"MIRA_MARKET_DATA_DEFAULT_SOURCE": "hithink_finance"},
                         clear=False):
        config.reset_cache()
        assert cli._effective_fetch_family("market_price") == "hithink_market_price"
        assert cli._effective_fetch_family("company_financials") == "company_financials"
    config.reset_cache()
    print("ok hithink postures are registered and the vendor provider routes market_price")


def main() -> int:
    test_resolve_thscode_infers_board()
    test_market_price_maps_snapshot_and_bars()
    test_statement_field_map_and_derived_ledger()
    test_calendar_epochs_are_beijing_midnight()
    test_cli_error_envelope_is_fetch_error()
    test_non_json_output_is_source_gap()
    test_registry_rows_and_routing()
    print("mira_data_hithink_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
