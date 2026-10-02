#!/usr/bin/env python3
"""Regression tests for the exchange-published Stock Connect turnover channel.

Offline: HTTP is patched. The tests pin the two vendors' different shapes, the dashed SZSE date
parameter, the bounded walk-back for non-trading days, the thousands-separator parsing, and -
most importantly - that **no net-flow metric exists**: the exchanges stopped publishing the
buy/sell split on 2024-08-16, so the honest output is turnover plus that caveat on every record.
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
from tools.mira_data.adapters import exchange_northbound as nb
from tools.mira_data.emit import emit_bundle


SSE_PAYLOAD = {"pageHelp": {"data": [
    {"totalVolume": "526.94", "totalAmount": "1,012.58", "tradeDate": "20260930",
     "etfTotalAmount": "19.54"}]}}
SZSE_BLOCKS = [{"metadata": {"catalogid": "SGT_SGTJYRB", "subname": "2026-09-30"},
                "data": [{"label": "当日交易总额（亿元人民币）", "total": "1,066.84"},
                         {"label": "当日ETF交易总额（亿元人民币）", "total": "16.12"},
                         {"label": "当日交易总笔数（万笔）", "total": "570.28"}]}]
SZSE_EMPTY = [{"metadata": {"subname": ""}, "data": []}]


def _run(venue="BOTH", *, sse=SSE_PAYLOAD, szse=None, **kwargs):
    captured = []
    dates = list(szse if isinstance(szse, list) else [SZSE_BLOCKS])

    def fake_get_json(url, **options):
        captured.append(url)
        if "szse.cn" in url:
            return dates.pop(0) if len(dates) > 1 else dates[0]
        return sse

    with mock.patch.object(nb.net, "get_json", side_effect=fake_get_json):
        result = nb.fetch_northbound_turnover(venue, as_of="2026-10-02", **kwargs)
    return result, captured


def test_both_venues_and_the_two_payload_shapes() -> None:
    result, urls = _run()
    metrics = {(r.provenance["venue"], r.metric): r for r in result.records}
    assert len(result.records) == 6
    sse_amount = metrics[("SSE", "northbound_turnover_amount")]
    assert sse_amount.value == 1012.58, "thousands separators must not survive parsing"
    assert sse_amount.unit == "CNY_100m" and sse_amount.currency == "CNY"
    assert sse_amount.family == "market_price" and sse_amount.research_object == "CN_SSE_NORTHBOUND"
    assert sse_amount.period == "2026-09-30" and sse_amount.posture.authority_level == "L2"
    assert sse_amount.posture.source_id == "sse_northbound_api"
    szse_amount = metrics[("SZSE", "northbound_turnover_amount")]
    assert szse_amount.value == 1066.84
    assert szse_amount.posture.source_id == "szse_northbound_api"
    assert metrics[("SZSE", "northbound_trade_count")].unit == "10k_trades"
    assert metrics[("SZSE", "northbound_etf_amount")].value == 16.12
    assert any(nb.SSE_SQL_ID in url for url in urls)
    assert any(nb.SZSE_CATALOG in url for url in urls)
    print("ok both venues parse, separators are handled and each leg keeps its own source id")


def test_net_flow_is_never_derived_and_the_caveat_travels() -> None:
    result, _urls = _run("SSE")
    for record in result.records:
        assert "net_flow" not in record.metric, "net flow has no upstream; do not invent one"
        assert "2024-08-16" in record.provenance["netFlowDiscontinuedOn"]
        assert "rather than capital entering or leaving" in record.provenance["netFlowUnavailable"]
    text = " ".join(record.claim_text for record in result.records)
    assert "净流入" not in text and "net flow" not in text
    print("ok turnover is emitted alone, with the discontinuation caveat on every record")


def test_szse_requires_the_dashed_date_and_walks_back() -> None:
    _result, urls = _run("SZSE", date="2026-10-01")
    txt = urls[0].split("txtDate=")[1].split("&")[0]
    assert txt == "2026-10-01", "the date parameter must be the dashed form"
    assert "%2D" in urls[0] or "2026-10-01" in urls[0]

    result, urls = _run("SZSE", date="2026-10-01", szse=[SZSE_EMPTY, SZSE_BLOCKS])
    assert len(urls) == 2, "an empty session must walk back one day, not give up"
    assert urls[1].split("txtDate=")[1].split("&")[0] == "2026-09-30"
    assert result.records[0].provenance.get("backfilledFrom") == "2026-10-01"
    assert result.records[0].provenance.get("usedTradeDate") == "2026-09-30"
    print("ok the SZSE leg pins a dashed date and walks back a bounded number of days")


def test_failures_and_venue_validation() -> None:
    try:
        nb.fetch_northbound_turnover("HKEX")
    except net.FetchError as exc:
        assert "invalid_venue" in str(exc)
    else:
        raise AssertionError("an unknown venue must be rejected")

    try:
        _run("SSE", sse={"pageHelp": {"data": []}})
    except net.FetchError as exc:
        assert "northbound_source_gap" in str(exc)
    else:
        raise AssertionError("an empty SSE payload must be a labelled gap")

    try:
        _run("SZSE", date="2026-10-01", szse=[SZSE_EMPTY])
    except net.FetchError as exc:
        assert "YYYY-MM-DD" in str(exc)
    else:
        raise AssertionError("a persistent empty SZSE series must name the date format")
    print("ok bad venues and empty payloads degrade with explicit labels")


def test_emitted_rows_and_registry() -> None:
    result, _urls = _run()
    scratch = ROOT / "local"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as out:
        emitted = emit_bundle(result.records, out_dir=out, research_object="CN_SSE_NORTHBOUND",
                              market_scope="CN",
                              endpoint=nb.ENDPOINT.format(symbol="SSE"))
        assert emitted["calculation_ledger"] is None
        with open(Path(out) / "evidence-log.csv", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    assert {row["claim_type"] for row in rows} == {"fact"}
    assert {row["authority_level"] for row in rows} == {"L2"}
    assert {row["verification_status"] for row in rows} == {"verified"}
    assert all("netFlowUnavailable=" in row["notes"] for row in rows)

    with (ROOT / "data" / "source-registry.csv").open(encoding="utf-8", newline="") as fh:
        registry = [row for row in csv.DictReader(fh)]
    with (ROOT / "data" / "source-class-map.csv").open(encoding="utf-8", newline="") as fh:
        mapping = {row["source_id"]: row for row in csv.DictReader(fh)}
    for source_id in ("sse_northbound_api", "szse_northbound_api"):
        rows = [row for row in registry if row["source_id"] == source_id]
        assert len(rows) == 1 and rows[0]["authority_level"] == "L2"
        assert "2024-08-16" in rows[0]["notes"]
        assert mapping[source_id]["source_class"] == "regulatory_and_exchange"
    print("ok turnover rows emit as L2 verified facts with the caveat in the evidence log")


def main() -> int:
    test_both_venues_and_the_two_payload_shapes()
    test_net_flow_is_never_derived_and_the_caveat_travels()
    test_szse_requires_the_dashed_date_and_walks_back()
    test_failures_and_venue_validation()
    test_emitted_rows_and_registry()
    print("mira_data_northbound_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
