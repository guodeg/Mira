#!/usr/bin/env python3
"""Regression tests for the SSE/SZSE margin-financing adapters.

Offline: the HTTP layer is patched. The tests pin the four things that make this
data easy to get quietly wrong — the two exchanges use different units AND
different field vocabularies, SZSE has no per-name filter (so a single-name read has
to binary-search a code-sorted full-market file), the exchanges publish on a lag
(so a bounded backfill must record both the requested and the used date), and the
Beijing exchange is not covered by either endpoint.
"""

from __future__ import annotations

import csv
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import config, net
from tools.mira_data.adapters import exchange_margin as xm
from tools.mira_data.emit import emit_bundle


SSE_DETAIL = {"result": [{
    "opDate": "20260930", "stockCode": "600519", "securityAbbr": "贵州茅台",
    "rzye": 17233521533, "rzmre": 300349909, "rzche": 385446299,
    "rqyl": 202648, "rqmcl": 1100, "rqchl": 11300, "rqylje": None, "rzrqjyzl": None,
}]}
SSE_SUMMARY = {"result": [{
    "opDate": "20260930", "rzye": 1296520652053, "rzmre": 62006897126,
    "rzche": 84968716127, "rqyl": 3220216902, "rqmcl": 55369387,
    "rqylje": 18892593376, "rzrqjyzl": 1315413245429,
}]}
SZSE_MARKET = [
    {"metadata": {"name": "融资融券交易总量", "pagecount": 0, "recordcount": 0},
     # Market table: 融券余额 (jrrjye) closes the identity in 亿元.
     "data": [{"jrrzmr": "879.59", "jrrzye": "12,657.14", "jrrjmc": "0.23",
               "jrrjyl": "11.88", "jrrjye": "108.15", "jrrzrjye": "12,765.29"}]},
    {"metadata": {"name": "融资融券交易明细", "pagecount": 106, "recordcount": 2105},
     # Detail table: the same field is 万元 — 45.80亿 + 11,559.99万 = 46.96亿.
     "data": [{"zqdm": "000001", "zqjc": "平安银行", "jrrzmr": "0.55",
               "jrrzye": "45.80", "jrrjmc": "15.27", "jrrjyl": "988.03",
               "jrrjye": "11,559.99", "jrrzrjye": "46.96"}]},
]


def _with_env(**env):
    return mock.patch.dict(os.environ, {k: str(v) for k, v in env.items()}, clear=False)


def _szse_detail_pages(pages: int = 106, codes_per_page: int = 20):
    """Fake SZSE detail table: code-sorted pages, matching the real ordering."""
    def table(page: int) -> dict:
        first = (page - 1) * codes_per_page + 1
        rows = [{"zqdm": f"{first + i:06d}", "zqjc": f"股{first + i}",
                 "jrrzye": "1.00", "jrrzmr": "0.10", "jrrjyl": "1.00",
                 "jrrjmc": "0.01", "jrrjye": "0.01", "jrrzrjye": "1.01"}
                for i in range(codes_per_page)]
        return {"metadata": {"name": "融资融券交易明细", "pagecount": pages},
                "data": rows}
    return table


def test_sse_name_reports_published_units() -> None:
    with mock.patch.object(xm.net, "get_json", return_value=SSE_DETAIL):
        result = xm.fetch_margin_balance("600519", date="2026-09-30", as_of="2026-10-02")
    metrics = {r.metric: r for r in result.records}
    assert metrics["margin_financing_balance"].value == 17233521533
    assert metrics["margin_financing_balance"].unit == "CNY"
    assert metrics["margin_short_balance_volume"].value == 202648
    assert metrics["margin_short_balance_volume"].unit == "shares"
    # Null fields are skipped rather than reported as zero.
    assert "margin_short_balance_value" not in metrics
    assert "margin_total_balance" not in metrics
    first = metrics["margin_financing_balance"]
    assert first.family == "ownership_short_interest"
    assert first.posture.source_id == "sse_margin_api"
    assert first.posture.authority_level == "L2"
    assert first.provenance["vendorField"] == "rzye"
    assert first.provenance["vendorValue"] == 17233521533
    assert first.period == "2026-09-30"
    print("ok SSE per-name margin maps to L2 records in the published units")


def test_szse_values_are_normalised_from_yi_and_wan() -> None:
    def fake_table(date, page=1, table=1, **kwargs):
        if table == 1:
            return SZSE_MARKET[0]
        # One page holding 平安银行, as the real file does on its first page.
        return {"metadata": {"pagecount": 1}, "data": SZSE_MARKET[1]["data"]}

    with _with_env(MIRA_MARGIN_MAX_BACKFILL_DAYS=0), \
            mock.patch.object(xm, "_szse_table", side_effect=fake_table):
        config.reset_cache()
        result = xm.fetch_margin_balance("000001", date="2026-09-18", as_of="2026-10-02")
    config.reset_cache()
    metrics = {r.metric: r for r in result.records}
    # 亿元 -> 元 and 万股 -> 股: 45.80亿元 = 4.58e9元, 988.03万股 = 9,880,300股.
    assert metrics["margin_financing_balance"].value == 45.80 * 1e8
    assert metrics["margin_short_balance_volume"].value == 988.03 * 1e4
    # 融券余额 is 万元 in the detail table: 11,559.99万 = 1.156e8元, not 1.156e12.
    assert metrics["margin_short_balance_value"].value == 11559.99 * 1e4
    assert "inferred_from_identity" in metrics["margin_short_balance_value"].provenance["unitBasis"]
    provenance = metrics["margin_financing_balance"].provenance
    assert provenance["vendorValue"] == "45.80"
    assert provenance["vendorUnit"] == "CNY_100m/10000_shares"
    assert metrics["margin_financing_balance"].posture.source_id == "szse_margin_api"
    print("ok SZSE 亿元/万股 are normalised to 元/股 with the raw value kept")


def test_szse_per_name_uses_a_bounded_search() -> None:
    table_pages = _szse_detail_pages()
    calls: list[int] = []

    def fake_table(date, page=1, table=1, **kwargs):
        calls.append(page)
        return table_pages(page)

    with _with_env(MIRA_MARGIN_MAX_SEARCH_PAGES=12), \
            mock.patch.object(xm, "_szse_table", side_effect=fake_table):
        config.reset_cache()
        found = xm._szse_find_code("001500", "2026-09-18", {"metadata": {"pagecount": 106}})
        missing = xm._szse_find_code("999999", "2026-09-18", {"metadata": {"pagecount": 106}})
    config.reset_cache()
    assert found is not None and found["zqdm"] == "001500"
    assert missing is None
    assert len(calls) <= 2 * 12, "search must stay inside its request budget"
    assert len(calls) < 106, "the full market file must never be pulled page by page"
    print("ok SZSE single-name lookup binary-searches instead of pulling the market")


def test_backfill_records_requested_and_used_dates() -> None:
    def fake_get_json(url, **kwargs):
        # Nothing published for the requested day; the previous day has data.
        if "20260930" in url:
            return {"result": []}
        return SSE_DETAIL

    with _with_env(MIRA_MARGIN_MAX_BACKFILL_DAYS=3), \
            mock.patch.object(xm.net, "get_json", side_effect=fake_get_json):
        config.reset_cache()
        result = xm.fetch_margin_balance("600519", date="2026-09-30", as_of="2026-10-02")
    config.reset_cache()
    record = result.records[0]
    assert record.provenance["requestedDate"] == "2026-09-30"
    assert record.provenance["tradeDate"] == "2026-09-30"    # from the SSE opDate field
    assert record.source_date == "2026-09-30"
    print("ok a lagging publication keeps the requested date visible in provenance")


def test_bj_names_degrade_without_a_request() -> None:
    called = {"n": 0}

    def fake_get_json(*args, **kwargs):
        called["n"] += 1
        return {}

    with mock.patch.object(xm.net, "get_json", side_effect=fake_get_json):
        try:
            xm.fetch_margin_balance("830799", date="2026-09-30")
        except net.FetchError as exc:
            assert "margin_source_gap" in str(exc) and "BSE" in str(exc)
        else:
            raise AssertionError("expected a source gap for a Beijing exchange name")
    assert called["n"] == 0, "an uncovered venue must not spend a request"
    print("ok Beijing-exchange names degrade to a labelled gap with no request spent")


def test_market_summary_is_a_macro_series_for_both_exchanges() -> None:
    with _with_env(MIRA_MARGIN_MAX_BACKFILL_DAYS=0), \
            mock.patch.object(xm.net, "get_json", return_value=SSE_SUMMARY), \
            mock.patch.object(xm, "_szse_table",
                              side_effect=lambda date, page=1, table=1: SZSE_MARKET[table - 1]):
        config.reset_cache()
        result = xm.fetch_margin_market("BOTH", date="2026-09-30", as_of="2026-10-02")
    config.reset_cache()
    objects = {r.research_object for r in result.records}
    assert objects == {"SSE_MARGIN", "SZSE_MARGIN"}
    assert {r.family for r in result.records} == {"macro_series"}
    assert {r.posture.authority_level for r in result.records} == {"L2"}
    sse = next(r for r in result.records if r.research_object == "SSE_MARGIN"
               and r.metric == "margin_financing_balance")
    assert sse.value == 1296520652053
    szse = next(r for r in result.records if r.research_object == "SZSE_MARGIN"
                and r.metric == "margin_short_balance_value")
    # Market table carries 融券余额 in 亿元: 108.15亿 = 1.0815e10元.
    assert szse.value == 108.15 * 1e8
    assert "亿元" in szse.provenance["unitBasis"]
    print("ok both exchanges' market aggregates emit as L2 macro series")


def test_szse_rjye_unit_is_inferred_per_table() -> None:
    """The same vendor field is 万元 in the detail table and 亿元 in the market table."""
    detail_row = SZSE_MARKET[1]["data"][0]
    market_row = SZSE_MARKET[0]["data"][0]
    detail_value, detail_basis = xm._szse_rjye(dict(detail_row), 2)
    market_value, market_basis = xm._szse_rjye(dict(market_row), 1)
    assert detail_value == 11559.99 * 1e4, detail_value
    assert "万元" in detail_basis
    assert market_value == 108.15 * 1e8, market_value
    assert "亿元" in market_basis

    # Without the identity fields the table default applies, and it is labelled as such.
    fallback_value, fallback_basis = xm._szse_rjye({"jrrjye": "10.00"}, 2)
    assert fallback_value == 10.0 * 1e4
    assert fallback_basis.startswith("table_default")
    print("ok 融券余额 unit is inferred from the vendor identity, per table")


def test_emitted_rows_are_l2_exchange_facts() -> None:
    with mock.patch.object(xm.net, "get_json", return_value=SSE_DETAIL):
        result = xm.fetch_margin_balance("600519", date="2026-09-30", as_of="2026-10-02")
    scratch = ROOT / "local"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as out:
        emitted = emit_bundle(result.records, out_dir=out, research_object="600519.SH",
                              market_scope="CN", endpoint="sse-margin://queryMargin.do/600519.SH")
        assert emitted["calculation_ledger"] is None
        with open(Path(out) / "evidence-log.csv", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    assert {row["claim_type"] for row in rows} == {"fact"}
    assert {row["authority_level"] for row in rows} == {"L2"}
    assert {row["verification_status"] for row in rows} == {"verified"}
    assert {row["source_speaker"] for row in rows} == {"exchange"}
    assert {row["source_id"] for row in rows} == {"sse_margin_api"}
    assert all("vendorValue=" in row["notes"] for row in rows)
    print("ok emitted margin rows are verified L2 exchange facts with the raw value in notes")


def test_registry_rows_are_registered() -> None:
    with (ROOT / "data" / "source-registry.csv").open(encoding="utf-8", newline="") as fh:
        registry = [row["source_id"] for row in csv.DictReader(fh)]
    with (ROOT / "data" / "source-class-map.csv").open(encoding="utf-8", newline="") as fh:
        mapping = {row["source_id"]: row["source_class"] for row in csv.DictReader(fh)}
    for source_id in ("sse_margin_api", "szse_margin_api"):
        assert registry.count(source_id) == 1, f"{source_id} must appear once in the registry"
        assert mapping[source_id] == "regulatory_and_exchange"
    print("ok both exchange margin sources are registered with 1:1 class-map rows")


def main() -> int:
    test_sse_name_reports_published_units()
    test_szse_values_are_normalised_from_yi_and_wan()
    test_szse_per_name_uses_a_bounded_search()
    test_backfill_records_requested_and_used_dates()
    test_bj_names_degrade_without_a_request()
    test_market_summary_is_a_macro_series_for_both_exchanges()
    test_szse_rjye_unit_is_inferred_per_table()
    test_emitted_rows_are_l2_exchange_facts()
    test_registry_rows_are_registered()
    print("mira_data_margin_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
