"""CLI: fetch a canonical family for a symbol and emit the artifact bundle.

Usage:
    PYTHONPATH=tools python3 -m mira_data fetch company_financials AAPL --out private/data-smoke

Families wired in P1:
    company_financials   SEC companyfacts (US, fact/L2)
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from functools import partial

from . import config, fundamentals, net, screening, technical
from .adapters import (bea, bls, cboe_volatility, cftc_cot, chinamoney_rates,
                       cninfo_disclosure,
                       csindex_index,
                       eastmoney_consensus, eastmoney_macro, em_holders, em_insider, em_lockup,
                       exchange_disclosure, exchange_futures, exchange_margin, exchange_northbound,
                       futures_inventory, treasury_fiscal,
                       fred, futu_opend,
                       hithink_finance, hithink_options, hithink_valuation, ibkr_gateway,
                       investor_qa, nbs_stats, news_pointers, sec_companyfacts, yahoo_chart)
from .emit import emit_bundle

# Families that are mainland-A-share only and take a thscode-style symbol.
_A_SHARE_PREFIXES = ("hithink_", "cninfo_")
_A_SHARE_FAMILIES = ("consensus_estimate", "margin_balance", "margin_market",
                     "investor_qa", "macro_rates", "option_surface", "macro_china",
                     "macro_nbs", "macro_region", "index_benchmark", "index_members",
                     "index_valuation", "exchange_announcements", "northbound_turnover",
                     "shareholder_count", "ir_activity", "valuation_snapshot",
                     "executive_holdings", "lockup_schedule", "lockup_change",
                     "shareholders_top10", "shareholders_free_float")
# Families that take the announcement-window flags (--since/--until/--max-items).
# macro_fred is included because a FRED series read is likewise bounded by an observation
# window plus a row cap, and both map onto the provider's own parameters.
_WINDOW_FAMILIES = ("cninfo_announcements", "exchange_announcements", "ir_activity",
                    "executive_holdings", "lockup_schedule", "macro_fred",
                    "futures_member_rank", "futures_warehouse", "futures_basis",
                    "treasury_debt", "treasury_avg_interest", "cftc_cot")
# Market-level families whose symbol is a venue selector defaulting to both venues.
_VENUE_FAMILIES = {"northbound_turnover": "NORTHBOUND"}
# Families that take NO symbol at all. They are not venue selectors, so they must not fall into
# the "BOTH" default above - that default is a venue choice, and passing it as a symbol made
# `treasury_debt` receive the literal string "BOTH" as its dataset.
_SYMBOL_LESS_FAMILIES = ("treasury_debt", "treasury_avg_interest", "cftc_cot")
# Families whose "symbol" is a security (thscode-resolvable) rather than a venue name.
_THSCODE_FAMILIES = ("consensus_estimate", "margin_balance", "investor_qa", "option_surface")
# Market-level families: the "symbol" selects a venue or benchmark, defaulting to both.
_MARKET_SERIES_FAMILIES = {"margin_market": "MARGIN", "macro_rates": "RATES",
                           "macro_china": "MACRO", "macro_nbs": "NBS",
                           "macro_region": "REGION", "northbound_turnover": "NORTHBOUND",
                           "macro_fred": "FRED", "macro_bea": "BEA",
                           "futures_member_rank": "FUTURES",
                           "futures_warehouse": "FUTURES", "futures_basis": "FUTURES",
                           "treasury_debt": "TREASURY", "treasury_avg_interest": "TREASURY",
                           "cftc_cot": "CFTC", "cboe_volatility": "CBOE", "cboe_implied_vol": "CBOE"}

def _is_a_share_family(family: str) -> bool:
    return family.startswith(_A_SHARE_PREFIXES) or family in _A_SHARE_FAMILIES


def _is_thscode_family(family: str) -> bool:
    return family.startswith(_A_SHARE_PREFIXES) or family in _THSCODE_FAMILIES

FETCHERS = {
    "company_financials": ("SEC companyfacts", sec_companyfacts.fetch_company_financials,
                           sec_companyfacts.COMPANYFACTS_URL),
    "market_price": ("Yahoo v8 chart", yahoo_chart.fetch_market_price,
                     yahoo_chart.CHART_URL),
    "macro_series": ("BLS public data", bls.fetch_macro_series, bls.SERIES_URL),
    "macro_fred": ("FRED official macro/rates series (keyed)", fred.fetch_macro_series,
                   fred.OBSERVATIONS_URL),
    "macro_bea": ("BEA official national/industry accounts (keyed)", bea.fetch_macro_dataset,
                  bea.DATA_URL),
    "futures_member_rank": ("Exchange-published futures member rankings: SHFE / CZCE / CFFEX (L2)",
                            exchange_futures.fetch_member_rankings,
                            exchange_futures.SHFE_PM_URL),
    "futures_warehouse": ("Futures registered warehouse receipts (仓单), relayed (L5)",
                          futures_inventory.fetch_warehouse_receipts,
                          futures_inventory.EM_ENDPOINT),
    "futures_basis": ("Futures main-continuous basis: spot vs close/settle (L5 vendor)",
                      futures_inventory.fetch_basis, futures_inventory.BASIS_ENDPOINT),
    "treasury_debt": ("US Treasury Debt to the Penny: total public debt outstanding (L2)",
                      treasury_fiscal.fetch_treasury_series, treasury_fiscal.DEBT_URL),
    "treasury_avg_interest": ("US Treasury average interest rates by security type (L2)",
                              treasury_fiscal.fetch_avg_interest,
                              treasury_fiscal.AVG_INTEREST_URL),
    "cftc_cot": ("CFTC Commitments of Traders positioning (L2)", cftc_cot.fetch_cot_family,
                 cftc_cot.SOCRATA_URL),
    "cboe_volatility": ("CBOE volatility index history: 19 series incl. VIX complex (L2)",
                        cboe_volatility.fetch_volatility_index, cboe_volatility.ENDPOINT),
    "cboe_implied_vol": ("CBOE published 30-day implied volatility (iv30) and index level (L2)",
                         cboe_volatility.fetch_implied_volatility, cboe_volatility.QUOTE_ENDPOINT),
    "ibkr_market_price": ("IBKR local Gateway", ibkr_gateway.fetch_market_price,
                          ibkr_gateway.GATEWAY_ENDPOINT),
    "ibkr_positions": ("IBKR local Gateway positions", ibkr_gateway.fetch_positions,
                       ibkr_gateway.GATEWAY_ENDPOINT),
    "ibkr_account_summary": ("IBKR local Gateway account summary",
                             ibkr_gateway.fetch_account_summary,
                             ibkr_gateway.GATEWAY_ENDPOINT),
    "ibkr_historical_bars": ("IBKR local Gateway historical bars",
                             ibkr_gateway.fetch_historical_bars,
                             ibkr_gateway.GATEWAY_ENDPOINT),
    "futu_market_price": ("Futu OpenD local Gateway", futu_opend.fetch_market_price,
                          futu_opend.OPEND_ENDPOINT),
    "futu_historical_bars": ("Futu OpenD local Gateway historical bars",
                             futu_opend.fetch_historical_bars,
                             futu_opend.OPEND_ENDPOINT),
    "futu_option_chain": ("Futu OpenD local Gateway option chain",
                          futu_opend.fetch_option_chain,
                          futu_opend.OPEND_ENDPOINT),
    "futu_future_info": ("Futu OpenD local Gateway futures info",
                         futu_opend.fetch_future_info,
                         futu_opend.OPEND_ENDPOINT),
    "hithink_market_price": ("Tonghuashun A-share market data (hithink-finance CLI)",
                             hithink_finance.fetch_market_price,
                             hithink_finance.SNAPSHOT_ENDPOINT),
    "hithink_company_financials": ("Tonghuashun A-share standardized statements "
                                   "(hithink-finance CLI)",
                                   hithink_finance.fetch_company_financials,
                                   hithink_finance.FINANCIALS_ENDPOINT),
    "cninfo_announcements": ("CNINFO A-share announcement index (L1 primary disclosure)",
                             cninfo_disclosure.fetch_issuer_disclosures,
                             cninfo_disclosure.ENDPOINT),
    "consensus_estimate": ("Eastmoney sell-side consensus (L5 expectation baseline)",
                           eastmoney_consensus.fetch_consensus,
                           eastmoney_consensus.ENDPOINT),
    "margin_balance": ("SSE/SZSE margin financing & securities lending (L2 exchange data)",
                       exchange_margin.fetch_margin_balance,
                       exchange_margin.SSE_ENDPOINT),
    "margin_market": ("SSE/SZSE market-wide margin aggregates (L2 exchange data)",
                      exchange_margin.fetch_margin_market,
                      exchange_margin.SSE_ENDPOINT),
    "investor_qa": ("Investor Q&A: 互动易 for Shenzhen, 上证e互动 for Shanghai (L2 platform)",
                    investor_qa.fetch_investor_qa,
                    investor_qa.IRM_ENDPOINT),
    "macro_rates": ("CFETS benchmark rates: LPR and Shibor (L2 official macro)",
                    chinamoney_rates.fetch_macro_rates,
                    chinamoney_rates.LPR_ENDPOINT),
    "option_surface": ("A-share ETF options surface (L5, via hithink-finance CLI)",
                       hithink_options.fetch_option_surface,
                       hithink_options.ENDPOINT),
    "macro_china": ("China CPI/PPI/GDP/PMI (L5 relay of official releases)",
                    eastmoney_macro.fetch_macro_china,
                    eastmoney_macro.ENDPOINT),
    "macro_nbs": ("Official NBS series: prices, PMI trio, industrial output/revenue, "
                  "property, investment, income, retail and GDP (L2)",
                  nbs_stats.fetch_macro_nbs, nbs_stats.ENDPOINT),
    "macro_region": ("Official NBS regional statistics: provincial GDP/income, "
                     "地级市 catalogue (L2)",
                     nbs_stats.fetch_nbs_region, nbs_stats.ENDPOINT),
    "index_benchmark": ("Official CSI index history and profile (中证指数公司, L2 benchmark)",
                        csindex_index.fetch_index_benchmark, csindex_index.ENDPOINT),
    "index_members": ("Official CSI index composition and weights (xls, needs xlrd)",
                      csindex_index.fetch_index_members, csindex_index.ENDPOINT),
    "index_valuation": ("Official CSI index valuation series: P/E and dividend yield (L2)",
                        csindex_index.fetch_index_valuation, csindex_index.ENDPOINT),
    "exchange_announcements": ("Exchange-direct announcement metadata: SSE or SZSE (L1)",
                               exchange_disclosure.fetch_exchange_announcements,
                               exchange_disclosure.SSE_ENDPOINT),
    "northbound_turnover": ("Exchange-published Stock Connect turnover, SSE/SZSE (L2)",
                            exchange_northbound.fetch_northbound_turnover,
                            exchange_northbound.ENDPOINT),
    "shareholder_count": ("Shareholder count from the filed periodic report body (L1)",
                          cninfo_disclosure.fetch_shareholder_count,
                          "https://static.cninfo.com.cn/finalpage/{symbol}.PDF"),
    "ir_activity": ("Filed IR-activity records (调研/业绩说明会) as transcript claims (L1)",
                    cninfo_disclosure.fetch_ir_activity,
                    "https://static.cninfo.com.cn/finalpage/{symbol}.PDF"),
    "valuation_snapshot": ("Stock-level valuation ratios PE/PB/PS/PCF, up to 100 names (L5)",
                           hithink_valuation.fetch_valuation_snapshot,
                           hithink_valuation.ENDPOINT),
    "executive_holdings": ("董监高持股变动 (executive shareholding changes) relay (L5)",
                           em_insider.fetch_executive_holdings, em_insider.ENDPOINT),
    "lockup_schedule": ("限售解禁 schedule: date, share type, batch holders (L5)",
                        em_lockup.fetch_lockup_schedule, em_lockup.ENDPOINT),
    "lockup_change": ("限售股份变动情况 from the filed periodic report body: per-holder "
                      "年初/解除/增加/年末 restricted shares (L1)",
                      cninfo_disclosure.fetch_lockup_change, cninfo_disclosure.ENDPOINT),
    "shareholders_top10": ("前十名股东 (top-10 holders by total holding, L5 relay)",
                           partial(em_holders.fetch_shareholders, table="top10"),
                           em_holders.TABLES["top10"][3]),
    "shareholders_free_float": ("前十名流通股东 (top-10 tradable-share holders, L5 relay)",
                                partial(em_holders.fetch_shareholders, table="free_float"),
                                em_holders.TABLES["free_float"][3]),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mira_data", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="fetch a canonical family and emit artifacts")
    f.add_argument("family", choices=sorted(FETCHERS))
    f.add_argument("symbol", nargs="?")
    f.add_argument("--out", default="private/data-smoke", help="output directory")
    f.add_argument("--as-of", default=None, help="as-of date YYYY-MM-DD (default today)")
    f.add_argument("--market-scope", default=None,
                   help="market scope (default: CN for hithink_* families, US otherwise)")
    f.add_argument("--cash-flow-span", choices=sec_companyfacts.CASH_FLOW_SPANS,
                   default="quarter",
                   help="SEC cash-flow metrics only: 'quarter' = latest clean single "
                        "quarter (default), 'ytd' = newest cumulative filing "
                        "reconciled to a quarter by differencing prior YTD rows")
    f.add_argument("--since", default=None, help="cninfo_announcements only: YYYY-MM-DD window start")
    f.add_argument("--until", default=None, help="cninfo_announcements only: YYYY-MM-DD window end")
    f.add_argument("--category", default="", choices=[""] + sorted(cninfo_disclosure.CATEGORY_TOKENS),
                   help="cninfo_announcements only: server-side announcement category")
    f.add_argument("--max-items", type=int, default=None,
                   help="cninfo_announcements only: cap on announcements read")
    f.add_argument("--date", default=None,
                   help="margin_* families only: trade date YYYY-MM-DD "
                        "(defaults to the latest published day)")
    f.add_argument("--days", type=int, default=None,
                   help="investor_qa only: keep answers from the last N days")
    f.add_argument("--expiry", default=None,
                   help="option_surface only: expiry month YYYY-MM (defaults to the nearest)")
    f.add_argument("--regions", default=None,
                   help="macro_region only: comma-separated region names, e.g. 北京,上海,广东")
    f.add_argument("--year", default=None,
                   help="macro_bea only: comma-separated years, e.g. 2024,2025,2026 "
                        "(defaults to the last 3)")
    f.add_argument("--dataset", default="NIPA",
                   help="macro_bea only: BEA dataset name (NIPA, NIUnderlyingDetail, "
                        "MNE, GDPbyIndustry, Regional, ...)")
    f.add_argument("--frequency", default="Q", choices=("Q", "A", "M"),
                   help="macro_bea only: Q (quarterly), A (annual) or M (monthly)")
    f.add_argument("--venue", default="SHFE", choices=("SHFE", "CZCE", "CFFEX"),
                   help="futures_member_rank only: which exchange publishes the variety")
    f.add_argument("--region-kind", default="province", choices=("province", "city"),
                   help="macro_region only: province (31) or major-city (71) catalog")
    f.add_argument("--no-weights", action="store_true",
                   help="index_members only: read the constituent list without the weight file")
    f.add_argument("--max-contracts", type=int, default=None,
                   help="option_surface only: cap on priced contracts")
    f.add_argument("--no-emit", action="store_true", help="print records only, don't write files")

    t = sub.add_parser("technical", help="compute technical context for a symbol")
    t.add_argument("symbol")
    t.add_argument("--benchmark", default="SPY")
    t.add_argument("--out", default="private/data-smoke")
    t.add_argument("--as-of", default=None)
    t.add_argument("--market-scope", default="US")
    t.add_argument("--no-emit", action="store_true", help="print summary only, don't write files")

    fd = sub.add_parser("fundamentals", help="compute fundamental deltas (YoY/CAGR) for a symbol")
    fd.add_argument("symbol")
    fd.add_argument("--out", default="private/data-smoke")
    fd.add_argument("--as-of", default=None)
    fd.add_argument("--market-scope", default="US")
    fd.add_argument("--no-emit", action="store_true", help="print deltas only, don't write files")

    sc = sub.add_parser(
        "screen",
        help="screen an explicit candidate list on fundamental criteria (bounded triage)")
    sc.add_argument("tickers",
                    help="comma-separated tickers, or @file with one ticker per line "
                         f"(max {screening.MAX_TICKERS})")
    sc.add_argument("--min-market-cap", type=float, default=None, help="USD floor")
    sc.add_argument("--min-fcf-yield", type=float, default=None,
                    help="(FY OCF - FY capex) / market cap floor, e.g. 0.04")
    sc.add_argument("--max-debt-to-equity", type=float, default=None,
                    help="long-term debt / equity ceiling, e.g. 1.0")
    sc.add_argument("--min-net-margin", type=float, default=None, help="FY net margin floor")
    sc.add_argument("--min-revenue-yoy", type=float, default=None,
                    help="latest same-period revenue YoY floor, e.g. 0.0")
    sc.add_argument("--out", default="private/data-smoke")
    sc.add_argument("--as-of", default=None)
    sc.add_argument("--market-scope", default="US")
    sc.add_argument("--no-emit", action="store_true", help="print results only, don't write files")

    sub.add_parser("config", help="show resolved data-substrate configuration")

    v = sub.add_parser("validate", help="validate an emitted artifact bundle")
    v.add_argument("dir")

    ib = sub.add_parser("ibkr", help="read-only IBKR Gateway utilities")
    ib_sub = ib.add_subparsers(dest="ibkr_cmd", required=True)

    acct = ib_sub.add_parser("accounts", help="list managed accounts, masked by default")
    acct.add_argument("--show-full", action="store_true",
                      help="print full account ids (console-sensitive)")

    snap = ib_sub.add_parser("position-snapshot", help="write a private position snapshot CSV")
    snap.add_argument("--account", default=None,
                      help="account id; default MIRA_IBKR_ACCOUNT, or ALL if unset")
    snap.add_argument("--out", default="private/portfolio",
                      help="private output directory")
    snap.add_argument("--as-of", default=None)

    futu = sub.add_parser("futu", help="read-only Futu OpenD utilities")
    futu_sub = futu.add_subparsers(dest="futu_cmd", required=True)
    futu_sub.add_parser("probe", help="connect to local Futu OpenD and close")

    hithink = sub.add_parser("hithink", help="read-only hithink-finance CLI utilities")
    hithink_sub = hithink.add_subparsers(dest="hithink_cmd", required=True)
    hithink_sub.add_parser("probe", help="check the hithink-finance CLI version and auth state")

    cninfo = sub.add_parser("cninfo", help="read-only CNINFO disclosure-portal utilities")
    cninfo_sub = cninfo.add_subparsers(dest="cninfo_cmd", required=True)
    cninfo_sub.add_parser("probe", help="check the org-id map and a live announcement query")
    cninfo_text = cninfo_sub.add_parser(
        "text", help="find filings by title and extract their PDF text layer")
    cninfo_text.add_argument("symbol", help="A-share code, e.g. 688008")
    cninfo_text.add_argument("--title", default=None,
                             help="only filings whose title contains this substring")
    cninfo_text.add_argument("--since", default=None, help="YYYY-MM-DD window start")
    cninfo_text.add_argument("--until", default=None, help="YYYY-MM-DD window end")
    cninfo_text.add_argument("--max-items", type=int, default=None,
                             help="cap on indexed announcements scanned")
    cninfo_text.add_argument("--max-pages", type=int, default=None,
                             help="page cap per PDF (default 40)")
    cninfo_text.add_argument("--limit", type=int, default=None,
                             help="cap on PDFs actually extracted (default 3)")
    cninfo_text.add_argument("--out", default=None,
                             help="directory for the extracted .txt files")

    nbs = sub.add_parser("nbs", help="read-only National Bureau of Statistics utilities")
    nbs_sub = nbs.add_subparsers(dest="nbs_cmd", required=True)
    nbs_search = nbs_sub.add_parser("search", help="find official indicator ids by keyword")
    nbs_search.add_argument("keyword", help="Chinese keyword, e.g. 居民消费价格指数")
    nbs_search.add_argument("--frequency", default="monthly",
                            choices=("monthly", "quarterly", "annual", "province_monthly",
                                     "province_quarterly", "province_annual",
                                     "city_monthly_price"))
    nbs_regions = nbs_sub.add_parser(
        "regions", help="list official region codes used by the macro_region family")
    nbs_regions.add_argument("--kind", default="province", choices=("province", "city"))

    news = sub.add_parser(
        "news", help="news pointers for one name (discovery only; never an evidence log)")
    news.add_argument("symbol", help="A-share code, e.g. 600519")
    news.add_argument("--days", type=int, default=None, help="look-back window (default 30)")
    news.add_argument("--limit", type=int, default=None, help="max pointers (default 30)")
    news.add_argument("--out", default=None,
                      help="write news-pointers.csv here (still no evidence log)")

    args = parser.parse_args(argv)
    if args.cmd == "fetch":
        return _do_fetch(args)
    if args.cmd == "technical":
        return _do_technical(args)
    if args.cmd == "fundamentals":
        return _do_fundamentals(args)
    if args.cmd == "screen":
        return _do_screen(args)
    if args.cmd == "config":
        return _do_config(args)
    if args.cmd == "validate":
        return _do_validate(args)
    if args.cmd == "ibkr":
        return _do_ibkr(args)
    if args.cmd == "futu":
        return _do_futu(args)
    if args.cmd == "hithink":
        return _do_hithink(args)
    if args.cmd == "cninfo":
        return _do_cninfo(args)
    if args.cmd == "nbs":
        return _do_nbs(args)
    if args.cmd == "news":
        return _do_news(args)
    parser.error("unknown command")
    return 2


def _do_fundamentals(args) -> int:
    try:
        records = fundamentals.compute_deltas(
            args.symbol, as_of=args.as_of, market_scope=args.market_scope)
    except net.FetchError as exc:
        print(f"source_gap: could not compute deltas for {args.symbol}: {exc}", file=sys.stderr)
        return 1

    print(f"# {args.symbol.upper()} fundamental deltas (derived from SEC companyfacts)")
    print(f"{'metric':<24}{'value':>12}  tier")
    for r in records:
        print(f"{r.metric:<24}{_fmt(r.value):>12}  {r.posture.claim_type}/{r.posture.authority_level}")

    if args.no_emit:
        return 0

    result = emit_bundle(
        records, out_dir=args.out, research_object=args.symbol.upper(),
        market_scope=args.market_scope, endpoint="derived://tools/mira_data/fundamentals",
        params=f"symbol={args.symbol.upper()}",
    )
    print("\n# emitted")
    for key in ("evidence_log", "calculation_ledger", "manifest", "ingestion_log"):
        if result.get(key):
            print(f"  {key:<20} {result[key]}")
    print(f"  derived={result['n_records']} ledgered={result['n_ledgered']} "
          f"(Mira-computed -> ledger required, §8)")
    return 0


def _do_screen(args) -> int:
    criteria = {name: getattr(args, name) for name in screening.CRITERIA
                if getattr(args, name) is not None}
    try:
        tickers = _parse_tickers(args.tickers)
        res = screening.screen_candidates(
            tickers, criteria, as_of=args.as_of, market_scope=args.market_scope)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except net.FetchError as exc:
        print(f"source_gap: screen could not run: {exc}", file=sys.stderr)
        return 1

    s = res.summary
    print(f"# screen {s['as_of']}: {s['n_candidates']} candidates -> "
          f"{s['pass']} pass / {s['fail']} fail / {s['data_gap']} data_gap")
    print(f"  criteria: {s['criteria']}")
    print(f"{'ticker':<12}{'status':<10}{'mkt_cap':>18}{'fcf_yld':>11}{'d/e':>11}"
          f"{'margin':>11}{'rev_yoy':>11}  {'cash_flow_end':<14}{'yoy_basis':<26}gaps")
    for row in res.rows:
        print(f"{row['ticker']:<12}{row['screen_status']:<10}"
              f"{_fmt_metric(row['market_cap_usd']):>18}{_fmt_metric(row['fcf_yield']):>11}"
              f"{_fmt_metric(row['debt_to_equity']):>11}{_fmt_metric(row['net_margin']):>11}"
              f"{_fmt_metric(row['revenue_yoy']):>11}  "
              f"{row['cash_flow_period_end']:<14}{row['revenue_yoy_period']:<26}"
              f"{row['data_gaps']}")
    mixed = [r["ticker"] for r in res.rows
             if r["cash_flow_period_end"] != "source_gap"
             and r["revenue_yoy_period"] != "source_gap"]
    if mixed:
        print("  basis note: fcf_yield/net_margin use annual fiscal periods while "
              "revenue_yoy uses a quarterly YoY — compare the two period columns "
              f"per row before ranking ({', '.join(mixed)})")

    if args.no_emit:
        return 0

    watchlist = screening.emit_watchlist_rows(args.out, res.rows)
    print(f"\n# emitted\n  {'watchlist':<20} {watchlist}")
    if res.derived:
        result = emit_bundle(
            res.derived, out_dir=args.out,
            research_object=f"SCREEN_{s['as_of']}", market_scope=args.market_scope,
            endpoint="derived://tools/mira_data/screening", params=s["criteria"],
        )
        for key in ("evidence_log", "calculation_ledger", "manifest", "ingestion_log"):
            if result.get(key):
                print(f"  {key:<20} {result[key]}")
        print(f"  derived={result['n_records']} ledgered={result['n_ledgered']} "
              f"(Mira-computed -> ledger required, §8)")
    else:
        print("  no passing ticker -> no derived bundle (watchlist only)")
    return 0


def _parse_tickers(arg: str) -> list[str]:
    if arg.startswith("@"):
        with open(arg[1:], encoding="utf-8") as fh:
            return [line.strip() for line in fh if line.strip() and not line.startswith("#")]
    return arg.split(",")


def _fmt_metric(v) -> str:
    if isinstance(v, float):
        return f"{v:,.0f}" if v > 1000 else f"{v:.4f}"
    return str(v)


def _do_technical(args) -> int:
    try:
        res = technical.compute_technical(
            args.symbol, benchmark=args.benchmark, as_of=args.as_of,
            market_scope=args.market_scope,
        )
    except net.FetchError as exc:
        print(f"source_gap: could not compute technical context for {args.symbol}: {exc}",
              file=sys.stderr)
        return 1

    s = res.summary
    print(f"# {args.symbol.upper()} technical context vs {args.benchmark.upper()} (as of {s['as_of']})")
    for key in ("trend_state", "ma_stack_state", "volume_state", "volatility_state",
                "positioning_risk", "technical_context_score"):
        print(f"  {key:<24}: {s[key]}")
    print(f"  {'relative_return_3m':<24}: {s['relative_return_3m']}")
    lv = s["key_levels"]
    print(f"  {'close / inval / trigger':<24}: {s['close_price']} / {lv['invalidation']} / {lv['trigger']}")
    ac = s["actionability"]
    print(f"  {'chase_risk_state':<24}: {ac['chase_risk_state']}")
    current_rr = _fmt_metric(ac["current_entry_rr"]) if ac["current_entry_rr"] is not None else "source_gap"
    pullback_rr = _fmt_metric(ac["pullback_entry_rr"]) if ac["pullback_entry_rr"] is not None else "source_gap"
    print(f"  {'current / pullback R:R':<24}: {current_rr} / {pullback_rr}")
    print(f"  {'preferred_wait_zone':<24}: {ac['preferred_wait_zone']}")

    if args.no_emit:
        return 0

    check_path = technical.emit_check_row(args.out, res.row)
    print(f"\n# emitted\n  {'technical_check':<20} {check_path}")
    if res.derived:
        result = emit_bundle(
            res.derived, out_dir=args.out, research_object=args.symbol.upper(),
            market_scope=args.market_scope, endpoint="derived://tools/mira_data/technical",
            params=f"symbol={args.symbol.upper()};benchmark={args.benchmark.upper()}",
        )
        for key in ("evidence_log", "calculation_ledger", "manifest", "ingestion_log"):
            if result.get(key):
                print(f"  {key:<20} {result[key]}")
        print(f"  derived={result['n_records']} ledgered={result['n_ledgered']} "
              f"(Mira-computed -> ledger required, §8)")
    return 0


def _do_validate(args) -> int:
    from .validate import validate_bundle

    issues = validate_bundle(args.dir)
    for issue in issues:
        print(f"  {issue.level}: {issue.msg}")
    errors = sum(1 for i in issues if i.level == "ERROR")
    warns = len(issues) - errors
    if not issues:
        print(f"OK: {args.dir} passed bundle validation")
    print(f"\n{errors} error(s), {warns} warning(s)")
    return 1 if errors else 0


def _do_ibkr(args) -> int:
    if args.ibkr_cmd == "accounts":
        try:
            accounts = ibkr_gateway.managed_accounts(mask=not args.show_full)
        except net.FetchError as exc:
            print(f"source_gap: could not list IBKR accounts: {exc}", file=sys.stderr)
            return 1
        print(f"# ibkr accounts ({len(accounts)})")
        for account in accounts:
            print(account)
        return 0

    if args.ibkr_cmd == "position-snapshot":
        try:
            path = ibkr_gateway.emit_position_snapshot(
                args.out, account=args.account, as_of=args.as_of)
        except net.FetchError as exc:
            print(f"source_gap: could not write IBKR position snapshot: {exc}", file=sys.stderr)
            return 1
        print("# emitted")
        print(f"  position_snapshot  {path}")
        print("  storage_scope      private")
        return 0

    print(f"error: unknown ibkr command {args.ibkr_cmd}", file=sys.stderr)
    return 2


def _do_futu(args) -> int:
    if args.futu_cmd == "probe":
        try:
            endpoint = futu_opend.probe()
        except net.FetchError as exc:
            print(f"source_gap: could not connect to Futu OpenD: {exc}", file=sys.stderr)
            return 1
        print("# futu opend")
        print(f"  endpoint           : {endpoint}")
        print("  connection         : ok")
        print("  mode               : quote_readonly")
        return 0

    print(f"error: unknown futu command {args.futu_cmd}", file=sys.stderr)
    return 2


def _do_hithink(args) -> int:
    if args.hithink_cmd == "probe":
        try:
            info = hithink_finance.probe()
        except net.FetchError as exc:
            print(f"source_gap: could not use the hithink-finance CLI: {exc}", file=sys.stderr)
            return 1
        print("# hithink-finance cli")
        print(f"  binary             : {info['binary']}")
        print(f"  package            : {info['package']}")
        print(f"  version            : {info['version']} (node {info['node']})")
        print(f"  auth_method        : {info['auth_method']}")
        print(f"  auth_profile       : {info['auth_profile']}")
        print(f"  auth_configured    : {info['auth_configured']}")
        print("  mode               : vendor_readonly")
        if not info["auth_configured"]:
            print("\nRun `hithink-finance auth login` before fetching remote A-share data.")
        return 0

    print(f"error: unknown hithink command {args.hithink_cmd}", file=sys.stderr)
    return 2


def _do_cninfo(args) -> int:
    if args.cninfo_cmd == "probe":
        try:
            info = cninfo_disclosure.probe()
        except net.FetchError as exc:
            print(f"source_gap: could not use the CNINFO disclosure portal: {exc}", file=sys.stderr)
            return 1
        print("# cninfo announcement index")
        print(f"  org_id_map_rows    : {info['org_ids']}")
        print(f"  sample             : {info['sample_code']} -> orgId {info['sample_org_id']}")
        print(f"  sample_total       : {info['sample_total']} announcements")
        print(f"  sample_first       : {info['sample_first']}")
        print("  mode               : public_readonly (no key, no cookie)")
        return 0

    if args.cninfo_cmd == "text":
        return _do_cninfo_text(args)

    print(f"error: unknown cninfo command {args.cninfo_cmd}", file=sys.stderr)
    return 2


def _do_cninfo_text(args) -> int:
    """Find filings by title substring, then extract their PDF text layers."""
    limit = args.limit if args.limit is not None else 3
    max_pages = args.max_pages if args.max_pages is not None else 40
    try:
        rows = cninfo_disclosure.find_announcements(
            args.symbol, since=args.since, until=args.until,
            title_contains=args.title, max_items=args.max_items)
    except net.FetchError as exc:
        print(f"source_gap: could not read the CNINFO announcement index: {exc}",
              file=sys.stderr)
        return 1

    if not rows:
        print(f"source_gap: no announcement for {args.symbol} matched "
              f"title~{args.title!r} in the requested window", file=sys.stderr)
        return 1

    print(f"# cninfo pdf text — {args.symbol}, {len(rows)} filing(s) matched"
          + (f" title~{args.title!r}" if args.title else ""))
    out_dir = args.out
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    extracted = 0
    failures = 0
    for row in rows:
        if extracted >= limit:
            remaining = len(rows) - extracted - failures
            print(f"  ... {remaining} more matched; raise --limit to extract them")
            break
        label = f"{row['date_bj']} {row['announcementTitle']}"
        try:
            got = cninfo_disclosure.extract_pdf_text(
                row["pdf_url"], max_pages=max_pages)
        except net.FetchError as exc:
            failures += 1
            # A gap is reported as a gap. It is never rendered as a successful
            # zero-character extraction, which would read like an absent section.
            print(f"  GAP   {label}\n        {exc}")
            continue
        extracted += 1
        note = f" (first {got['pages_read']}/{got['page_count']} pages)" if got["truncated"] else ""
        print(f"  OK    {label}\n        {got['chars']:,} chars{note}")
        if out_dir:
            safe = re.sub(r"[^\w\u4e00-\u9fff]+", "_", row["announcementTitle"])[:80]
            path = os.path.join(out_dir, f"{row['date_bj']}_{safe}.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(got["text"])
            print(f"        -> {path}")

    print(f"  extracted {extracted}, gapped {failures} of {len(rows)} matched")
    return 0 if extracted else 1


def _do_nbs(args) -> int:
    if args.nbs_cmd == "regions":
        try:
            rows = nbs_stats.list_regions(args.kind)
        except net.FetchError as exc:
            print(f"source_gap: could not read the NBS region catalogue: {exc}", file=sys.stderr)
            return 1
        print(f"# nbs {args.kind} regions: {len(rows)}")
        for row in rows:
            print(f"  {row['code']}  {row['name']}")
        return 0

    if args.nbs_cmd == "search":
        try:
            rows = nbs_stats.search_indicators(args.keyword, frequency=args.frequency)
        except net.FetchError as exc:
            print(f"source_gap: could not search the NBS data library: {exc}", file=sys.stderr)
            return 1
        print(f"# nbs {args.frequency} indicators matching {args.keyword!r}: {len(rows)}")
        for row in rows[:40]:
            print(f"  {row['indicatorId']}  cid={row['catalogId']}  {str(row['name'])[:52]}")
            if row.get("mark"):
                print(f"      口径: {str(row['mark'])[:110]}")
        if not rows:
            print("  (no match; try a shorter keyword or another --frequency)")
        return 0

    print(f"error: unknown nbs command {args.nbs_cmd}", file=sys.stderr)
    return 2


def _do_news(args) -> int:
    try:
        pointers = news_pointers.fetch_news_pointers(
            args.symbol, days=args.days, limit=args.limit)
    except net.FetchError as exc:
        print(f"source_gap: could not read news pointers for {args.symbol}: {exc}",
              file=sys.stderr)
        return 1
    print(f"# news pointers for {args.symbol.upper()} ({len(pointers)} headlines)")
    print("# discovery only: no claim rows, no evidence log — read the primary before citing")
    for pointer in pointers:
        print(f"  {pointer['date']}  {pointer['outlet'][:14]:<16}{pointer['title'][:56]}")
        print(f"      {pointer['url']}")
    print("\n# primary routes to check")
    for hint in dict.fromkeys(pointer["primary_route_hint"] for pointer in pointers):
        print(f"  {hint}")
    if args.out:
        path = news_pointers.write_pointers(pointers, args.out)
        print(f"\n# wrote {path} (pointers only — no manifest, no ingestion log, no evidence log)")
    return 0


def _do_config(_args) -> int:
    ua, configured = config.contact_ua()
    print("# mira_data config")
    print(f"  contact_configured : {configured}")
    print(f"  user_agent         : {ua}")
    for key in (
        "MIRA_CONTACT_EMAIL", "MIRA_CONTACT_NAME", "FRED_API_KEY", "BEA_API_KEY",
        "MIRA_IBKR_HOST", "MIRA_IBKR_PORT", "MIRA_IBKR_CLIENT_ID",
        "MIRA_IBKR_ACCOUNT", "MIRA_IBKR_READONLY", "MIRA_IBKR_MARKET_DATA_TYPE",
        "MIRA_FUTU_HOST", "MIRA_FUTU_PORT", "MIRA_FUTU_DEFAULT_MARKET",
        "MIRA_FUTU_CURRENCY",
        "MIRA_HITHINK_BIN", "MIRA_HITHINK_TIMEOUT",
        "MIRA_CNINFO_TIMEOUT", "MIRA_CNINFO_PAGE_SLEEP", "MIRA_CNINFO_MAX_ITEMS",
        "MIRA_MARGIN_MAX_BACKFILL_DAYS", "MIRA_MARGIN_MAX_SEARCH_PAGES",
        "MIRA_QA_MAX_ITEMS", "MIRA_QA_MAX_UID_PAGES", "MIRA_QA_MAX_FEED_PAGES",
        "MIRA_RATES_MAX_OBSERVATIONS", "MIRA_RATES_WINDOW_DAYS",
        "MIRA_OPTIONS_MAX_CONTRACTS", "MIRA_OPTIONS_SCAN_ROWS", "MIRA_OPTIONS_QUOTE_DAYS",
        "MIRA_MACRO_MAX_OBSERVATIONS", "MIRA_NBS_MONTHS", "MIRA_NBS_CACHE_DAYS",
        "MIRA_NBS_PAUSE", "MIRA_NEWS_MAX_ITEMS", "MIRA_NEWS_WINDOW_DAYS",
        "MIRA_CSINDEX_YEARS", "MIRA_EXCHANGE_WINDOW_DAYS", "MIRA_EXCHANGE_MAX_ITEMS",
        "MIRA_MARKET_DATA_DEFAULT_SOURCE", "MIRA_LIVE_MARKET_DATA_SOURCE",
        "MIRA_BROKER_DATA_PRIORITY", "MIRA_FUTU_ENABLED_MARKETS",
    ):
        print(f"  {key:<18} : {'set' if config.get(key) else '-'}")
    print(f"  searched files     : {', '.join(config._candidate_paths())}")
    if not configured:
        print("\n" + config.config_hint())
    return 0


def _do_fetch(args) -> int:
    family = _effective_fetch_family(args.family)
    label, fetcher, endpoint_tmpl = FETCHERS[family]
    market_scope = args.market_scope or _default_market_scope(family)
    cash_flow_span = getattr(args, "cash_flow_span", "quarter")
    if cash_flow_span != "quarter" and family != "company_financials":
        print(f"error: --cash-flow-span applies to the SEC company_financials family "
              f"(A-share statements are filed per period and need no YTD reconciliation), "
              f"not {family}", file=sys.stderr)
        return 2
    if family not in _WINDOW_FAMILIES and any(
            value not in (None, "", 0)
            for value in (args.since, args.until, args.category)):
        print(f"error: --since/--until/--category apply to the "
              f"{'/'.join(_WINDOW_FAMILIES)} families, not {family}", file=sys.stderr)
        return 2
    if args.max_items is not None and family not in _WINDOW_FAMILIES:
        print(f"error: --max-items applies to the {'/'.join(_WINDOW_FAMILIES)} families, "
              f"not {family}", file=sys.stderr)
        return 2
    if args.date and family not in {"margin_balance", "margin_market", "futures_member_rank"}:
        print(f"error: --date applies to the margin_balance/margin_market/futures_member_rank "
              f"families, not {family}", file=sys.stderr)
        return 2
    if args.days and family not in {"investor_qa", "futures_warehouse", "cftc_cot"}:
        print(f"error: --days applies to the investor_qa family, not {family}",
              file=sys.stderr)
        return 2
    if (args.expiry or args.max_contracts is not None) and family != "option_surface":
        print(f"error: --expiry/--max-contracts apply to the option_surface family, "
              f"not {family}", file=sys.stderr)
        return 2
    if args.regions and family != "macro_region":
        print(f"error: --regions applies to the macro_region family, not {family}",
              file=sys.stderr)
        return 2
    if args.no_weights and family != "index_members":
        print(f"error: --no-weights applies to the index_members family, not {family}",
              file=sys.stderr)
        return 2
    symbol = args.symbol
    if family in {"ibkr_positions", "ibkr_account_summary"} and not symbol:
        symbol = config.get("MIRA_IBKR_ACCOUNT", "ALL") or "ALL"
    elif family in _MARKET_SERIES_FAMILIES and family not in _SYMBOL_LESS_FAMILIES and not symbol:
        symbol = "BOTH"
    elif family in _SYMBOL_LESS_FAMILIES:
        # An optional positional symbol may still be given (cfct_cot takes a market filter).
        symbol = symbol or ""
    elif not symbol:
        print(f"error: fetch {args.family} requires a symbol", file=sys.stderr)
        return 2
    kwargs = {}
    if family == "company_financials":
        kwargs["cash_flow_span"] = cash_flow_span
    elif family == "cninfo_announcements":
        kwargs.update(since=args.since, until=args.until,
                      category=args.category or "", max_items=args.max_items)
    elif family in {"margin_balance", "margin_market"}:
        kwargs["date"] = args.date
    elif family == "exchange_announcements":
        kwargs.update(since=args.since, until=args.until, max_items=args.max_items)
    elif family == "ir_activity":
        kwargs.update(since=args.since, until=args.until, max_items=args.max_items)
    elif family == "executive_holdings":
        kwargs.update(since=args.since, until=args.until, max_items=args.max_items)
    elif family == "lockup_schedule":
        kwargs.update(since=args.since, until=args.until, max_items=args.max_items)
    elif family == "lockup_change":
        kwargs.update(since=args.since, until=args.until, max_items=args.max_items)
    elif family in {"shareholders_top10", "shareholders_free_float"}:
        kwargs["max_holders"] = args.max_items
    elif family == "investor_qa":
        kwargs.update(days=args.days, max_items=args.max_items)
    elif family == "option_surface":
        kwargs.update(expiry=args.expiry, max_contracts=args.max_contracts)
    elif family == "macro_region":
        kwargs.update(regions=[part for part in (args.regions or "").split(",") if part.strip()]
                      or None, kind=args.region_kind)
    elif family == "macro_fred":
        kwargs.update(limit=args.max_items,
                      observation_start=args.since, observation_end=args.until)
    elif family == "macro_bea":
        kwargs.update(year=args.year, dataset=args.dataset, frequency=args.frequency)
    elif family == "futures_member_rank":
        kwargs.update(venue=args.venue, date=args.date, rank_limit=args.max_items)
    elif family == "futures_warehouse":
        kwargs.update(days=args.days, max_items=args.max_items)
    elif family == "futures_basis":
        kwargs["max_items"] = args.max_items
    elif family in {"treasury_debt", "treasury_avg_interest"}:
        # The dataset is fixed per family, so an optional symbol here is a mistake rather
        # than a selector: reject it instead of silently treating it as a dataset name.
        if symbol:
            print(f"error: {family} takes no symbol (it reads a fixed Treasury dataset)",
                  file=sys.stderr)
            return 2
        kwargs["limit"] = args.max_items
    elif family == "cftc_cot":
        kwargs.update(weeks=args.days, max_items=args.max_items)
    elif family == "index_members":
        kwargs["with_weights"] = not args.no_weights
    try:
        res = fetcher(symbol, as_of=args.as_of, market_scope=market_scope, **kwargs)
    except net.FetchError as exc:
        print(f"source_gap: could not fetch {args.family} for {symbol}: {exc}", file=sys.stderr)
        return 1

    records = res.records
    if family.startswith("futu_"):
        display_object = _display_futu_symbol(symbol)
    elif family in _MARKET_SERIES_FAMILIES:
        display_object = f"{symbol.upper()}_{_MARKET_SERIES_FAMILIES[family]}"
    elif _is_thscode_family(family):
        try:
            display_object = hithink_finance.resolve_thscode(symbol)
        except net.FetchError as exc:
            print(f"source_gap: could not resolve {symbol}: {exc}", file=sys.stderr)
            return 1
    else:
        display_object = {
            "ibkr_positions": "IBKR_POSITIONS",
            "ibkr_account_summary": "IBKR_ACCOUNT_SUMMARY",
        }.get(family, symbol.upper())
    print(f"# {display_object} {family} via {label}  ({len(records)} claims)")
    print(f"{'metric':<30}{'value':>20}  {'unit':<20}{'period':<12}{'tier'}")
    for r in records:
        tier = f"{r.posture.claim_type}/{r.posture.authority_level}"
        print(f"{r.metric:<30}{_fmt(r.value):>20}  {r.unit:<20}{r.period:<12}{tier}")
    if res.series:
        print(f"  + series '{res.series['name']}' ({len(res.series['rows'])} rows)")

    if args.no_emit:
        return 0

    endpoint_symbol = (display_object
                       if _is_thscode_family(family) or family in _MARKET_SERIES_FAMILIES
                       else symbol.upper())
    endpoint = endpoint_tmpl.format(
        symbol=endpoint_symbol,
        thscode=endpoint_symbol,
        cik10="<cik>",
        series_id=symbol,
        **_local_endpoint_params(family),
    )
    private_provider = family.startswith(("ibkr_", "futu_"))
    vendor_provider = family.startswith("hithink_")
    ingestion_route = (
        "authorized_provider" if private_provider or vendor_provider else "public_on_demand"
    )
    if private_provider:
        must_refresh_if = (
            "new broker snapshot, session reconnect, entitlement change, "
            "or position/account change"
        )
    elif vendor_provider:
        must_refresh_if = (
            "new vendor snapshot, entitlement change, or a superseding issuer filing "
            "(cross-check L1/L2 disclosure before durable use)"
        )
    elif family.startswith("cninfo_"):
        must_refresh_if = (
            "new or amended issuer announcement, or the next periodic-report window "
            "(annual/Q1 by Apr 30, interim by Aug 31, Q3 by Oct 31)"
        )
    elif family == "consensus_estimate":
        must_refresh_if = (
            "analyst estimate revision, next earnings or guidance update, and before any "
            "expectation-delta claim (the vendor payload carries no as-of timestamp)"
        )
    elif family in {"margin_balance", "margin_market"}:
        must_refresh_if = (
            "next trading day's publication; the exchanges publish after the close and can "
            "lag by a day or more, so re-read before using the level as current positioning"
        )
    elif family == "investor_qa":
        must_refresh_if = (
            "new company reply on the platform, or the next periodic-report window; a Q&A "
            "answer is company commentary and needs an L1/L2 cross-check before it is "
            "treated as fact"
        )
    elif family == "macro_rates":
        must_refresh_if = (
            "next benchmark fixing (LPR on the 20th of each month, Shibor each trading day)"
        )
    elif family == "option_surface":
        must_refresh_if = (
            "next session's quote, or an underlying move that changes the ATM strike; the "
            "surface carries closing prices only, not open interest or implied volatility"
        )
    elif family == "macro_china":
        must_refresh_if = (
            "next official release (CPI/PPI/PMI monthly, GDP quarterly); the relay carries "
            "no publish timestamp, so re-read before quoting it as the current print"
        )
    elif family == "macro_nbs":
        must_refresh_if = (
            "next official release; unpublished periods come back as 无 rather than absent, "
            "so a value can appear later within the same period code"
        )
    elif family == "macro_region":
        must_refresh_if = (
            "next provincial release; a multi-region read returns the latest period only, so "
            "a history needs one region per call"
        )
    elif family in {"index_benchmark", "index_members", "index_valuation"}:
        must_refresh_if = (
            "next index close or the compiler's next composition/valuation file; the weights "
            "workbook can lag the constituent workbook, and the valuation workbook carries "
            "only the latest ~21 observations"
        )
    elif family == "exchange_announcements":
        must_refresh_if = (
            "next disclosure from the issuer; the window is explicit, so an older filing needs "
            "--since rather than a wider default"
        )
    elif family == "lockup_change":
        must_refresh_if = (
            "the next periodic report body; an issuer can mark the table 适用 in one report and "
            "不适用 in the next, so a holder list that a filing publishes can be absent from the "
            "newest one. The issuer's own standalone 限售股上市流通公告 is the per-event cross-check"
        )
    elif family in {"treasury_debt", "treasury_avg_interest"}:
        must_refresh_if = (
            "the next business day for the debt stock (published daily with a lag) or the next "
            "monthly release for average interest rates"
        )
    elif family == "cftc_cot":
        must_refresh_if = (
            "the next weekly COT release (positions as of Tuesday, published the following "
            "Friday), so a report read mid-week is already a week old"
        )
    elif family in {"futures_warehouse", "futures_basis"}:
        must_refresh_if = (
            "the next session; both are aggregator relays, and the official stock file is not "
            "reachable from this substrate, so cross-check against the exchange's own daily "
            "bulletin before a durable conclusion"
        )
    elif family == "futures_member_rank":
        must_refresh_if = (
            "the next session; the exchange publishes after the close and a session can be "
            "missing, so the adapter walks back a bounded number of days and records both "
            "the requested and the used date"
        )
    elif family in {"shareholders_top10", "shareholders_free_float"}:
        must_refresh_if = (
            "the next periodic-report window (annual/Q1 by Apr 30, interim by Aug 31, Q3 by "
            "Oct 31), or a new shareholding disclosure; the channel reads the newest period "
            "only, so the issuer's own top-ten table is the L1 cross-check before a durable "
            "ownership conclusion"
        )
    else:
        must_refresh_if = ""
    result = emit_bundle(
        records, out_dir=args.out, research_object=display_object,
        market_scope=market_scope, endpoint=endpoint,
        params=f"symbol={display_object}", series=res.series,
        ingestion_route=ingestion_route, must_refresh_if=must_refresh_if,
    )
    print("\n# emitted")
    for key in ("manifest", "evidence_log", "ingestion_log", "calculation_ledger", "series"):
        if result.get(key):
            print(f"  {key:<20} {result[key]}")
    print(f"  claims={result['n_records']} series_rows={result['n_series_rows']} "
          f"ledgered={result['n_ledgered']} (disclosed/market values need no ledger)")
    return 0


def _effective_fetch_family(family: str) -> str:
    if family == "market_price":
        provider = _default_market_provider()
        if provider == "futu_opend":
            return "futu_market_price"
        if provider == "hithink_finance":
            return "hithink_market_price"
    return family


def _default_market_scope(family: str) -> str:
    """A-share families default to CN; every other family keeps the US default."""
    return "CN" if _is_a_share_family(family) else "US"


def _default_market_provider() -> str:
    return (config.get("MIRA_MARKET_DATA_DEFAULT_SOURCE", "") or "").strip().lower()


def _local_endpoint_params(family: str) -> dict[str, str]:
    if family.startswith("futu_"):
        return {
            "host": config.get("MIRA_FUTU_HOST", "127.0.0.1") or "127.0.0.1",
            "port": config.get("MIRA_FUTU_PORT", "11111") or "11111",
        }
    if family == "futures_member_rank":
        # The endpoint template carries a session placeholder; the adapter resolves the
        # actual session (walking back when the requested one has not published), so the
        # manifest records the requested date and each claim records the used one.
        return {"date": "YYYYMMDD"}
    if family == "futures_warehouse":
        # Same shape: the template names the variety and the adapter resolves the window.
        return {"variety": "<variety>", "date": "YYYYMMDD"}
    if family == "futures_basis":
        return {}       # the vendor endpoint template carries no placeholders
    if family in {"treasury_debt", "treasury_avg_interest"}:
        # The template names the dataset; each family fixes its own.
        return {"dataset": "debt_to_penny" if family == "treasury_debt" else "avg_interest_rates"}
    if family == "cftc_cot":
        return {"dataset": "6dca-aqww"}
    return {
        "host": config.get("MIRA_IBKR_HOST", "127.0.0.1") or "127.0.0.1",
        "port": config.get("MIRA_IBKR_PORT", "7497") or "7497",
    }


def _display_futu_symbol(symbol: str) -> str:
    text = symbol.strip()
    if "." in text:
        market, code = text.split(".", 1)
        return f"{market.upper()}.{code.upper()}"
    futu_market = config.get("MIRA_FUTU_DEFAULT_MARKET", "US") or "US"
    return f"{futu_market.upper()}.{text.upper()}"


def _fmt(v) -> str:
    if isinstance(v, (int, float)):
        return f"{v:,}"
    return str(v)


if __name__ == "__main__":
    raise SystemExit(main())
