"""Generic equity screening over an explicit candidate list.

Fills the ``discovery_or_screening`` execution gap for "screen equities on
fundamental criteria" (e.g. high FCF yield, low leverage). Deliberately lite
(architecture/data-acquisition-upgrade.md §0):

- universe = an explicit candidate list (≤ ``MAX_TICKERS``), never a
  whole-market crawl (DATA_POLICY: on-demand read, no bulk crawling);
- dimensions = only what the existing substrate already supports — SEC
  companyfacts flows/balances (L2) plus the Yahoo last close (L5) for
  market cap on the US side, and the vendor's standardised statements plus its
  indicator set on the A-share side;
- flow metrics use the latest complete fiscal year, sidestepping the
  companyfacts Q2/Q3 year-to-date trap (see fundamentals.py) and, on the A-share
  side, the vendor's **cumulative** quarterly series, which reports "Q2" as the
  H1 total;
- results are screening-grade: Mira-derived ratios are L6 ``derived_calculation``
  records (ledgered, per §8) over L2+L5 upstreams. Verify against filings
  before any durable use.

**Two markets, one candidate list, no implicit conversion.** A batch must be
single-market: the same numeric threshold means USD against US tickers and CNY
against A-shares, so a mixed batch would apply thresholds about 7x apart under
one predicate. Mixed batches are refused rather than silently reinterpreted.

A ticker whose data cannot be fetched degrades to ``screen_status=data_gap``;
if every candidate degrades, the run raises ``FetchError`` so the caller falls
back to the routing rule (watchlist note + ``source_gap``, never an improvised
screen result).

**A criterion that has no data is a PARTIAL gap, not a failure.** For A-shares,
``min_market_cap`` and ``min_fcf_yield`` cannot be computed at all (no channel
exposes shares outstanding or total market value, and a derived share count is
demonstrably unreliable). A candidate missing only those is still decided on the
criteria that do have data, and the missing ones are named in ``data_gaps`` — 
because hard-failing would reject every A-share on a criterion that is
unavailable by construction.
"""

from __future__ import annotations

import csv
import datetime as _dt
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from . import cn_symbols, net
from .adapters import hithink_screen as hscr
from .adapters import sec_companyfacts as scf
from .adapters import yahoo_chart
from .canonical import POSTURES, CanonicalRecord
from .fundamentals import _yoy

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TEMPLATE = os.path.join(_REPO_ROOT, "templates", "screening-watchlist.csv")

MAX_TICKERS = 30      # bounded candidate triage, not a market crawl (DATA_POLICY)
_PAUSE_SECONDS = 0.4  # polite inter-ticker pacing (SEC fair-access cap is 10 req/s)
_STALE_DAYS = 550     # ~18 months: the latest complete FY can lag ~15 months; older
                      # observations must degrade to data_gap, not anchor a ratio
                      # against today's market cap (an issuer's discontinued metric
                      # would otherwise silently mix decade-old flows with a live price).

# criterion flag -> (metric column, direction). The metric set is exactly what
# the existing fundamentals + market_price adapters can compute — adding a
# criterion here must not require a new data channel.
CRITERIA: dict[str, tuple[str, str]] = {
    "min_market_cap": ("market_cap_usd", "min"),
    "min_fcf_yield": ("fcf_yield", "min"),
    "max_debt_to_equity": ("debt_to_equity", "max"),
    "min_net_margin": ("net_margin", "min"),
    "min_revenue_yoy": ("revenue_yoy", "min"),
}

# The A-share registry. Same names for the criteria that mean the same thing, so a caller's
# muscle memory works across markets; `max_assets_debt_ratio` is A-share-only because 资产负债率
# is the native A-share leverage convention and has no US counterpart here.
#
# `max_debt_to_equity` deliberately maps to the vendor's long-term-debt/equity indicator and NOT
# to total liabilities. The balance sheet's field named `total_debt` IS total liabilities (it
# equals assets - equity to the penny), so using it would filter out asset-light high-margin
# businesses whose liabilities are almost entirely interest-free payables — the opposite of what
# a leverage ceiling is for. See adapters/hithink_screen.py.
CRITERIA_CN: dict[str, tuple[str, str]] = {
    "min_market_cap": ("market_cap", "min"),
    "min_fcf_yield": ("fcf_yield", "min"),
    "max_debt_to_equity": ("debt_to_equity", "max"),
    "max_assets_debt_ratio": ("assets_debt_ratio", "max"),
    "min_net_margin": ("net_margin", "min"),
    "min_revenue_yoy": ("revenue_yoy", "min"),
}

_METRIC_COLUMNS = ("market_cap_usd", "fcf_yield", "debt_to_equity", "net_margin", "revenue_yoy")

# Columns shared by both markets. `market_cap_usd` stays as-is so existing US rows and the
# template keep working; the A-share market cap would be CNY, so it gets its own column rather
# than sharing a header whose name asserts a currency.
_CN_EXTRA_COLUMNS = ("market_cap_cny", "assets_debt_ratio", "leverage_caveat")

_TIER_NOTE = ("screening_grade: SEC companyfacts L2 + Yahoo price L5; Mira-derived "
              "ratios L6 (ledgered); verify vs filings before durable use")

_TIER_NOTE_CN = ("screening_grade: vendor standardised statements + indicator set L5; "
                 "Mira-derived ratios L6 (ledgered). Leverage is a VENDOR-PUBLISHED indicator "
                 "whose inputs no channel exposes, so it is taken as-is rather than recomputed; "
                 "verify vs the filed statements before durable use")

_NEXT_ACTION = {
    "pass": "single_equity_research_candidate",
    "partial": "single_equity_research_candidate_partial_data",
    "fail": "rejected_by_screen",
    "data_gap": "source_gap_refresh",
}


@dataclass
class ScreenResult:
    rows: list            # watchlist rows (template schema), one per candidate
    derived: list = field(default_factory=list)  # ledgered records for passing tickers
    summary: dict = field(default_factory=dict)


def template_columns() -> list:
    with open(_TEMPLATE, encoding="utf-8") as fh:
        return next(csv.reader(fh))


def emit_watchlist_rows(out_dir: str, rows: list) -> str:
    """Append rows to ``<out_dir>/screening-watchlist.csv`` (template schema)."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "screening-watchlist.csv")
    cols = template_columns()
    exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def screen_candidates(tickers: list, criteria: dict, *, as_of: Optional[str] = None,
                      market_scope: Optional[str] = None) -> ScreenResult:
    """Screen an explicit candidate list against fundamental criteria.

    ``criteria`` maps :data:`CRITERIA` flag names to thresholds. At least one
    criterion is required — a screen with no criteria is just a fetch.

    The market is inferred from the tickers and a batch must be single-market:
    ``market_scope`` may be passed to assert it, and a mismatch or a mixed batch
    raises rather than applying one numeric threshold under two currencies.
    """
    if not criteria:
        raise ValueError("at least one screening criterion is required")

    symbols = [t.strip().upper() for t in tickers if t and t.strip()]
    if not symbols:
        raise ValueError("empty candidate list")
    if len(symbols) > MAX_TICKERS:
        raise ValueError(
            f"candidate list too large ({len(symbols)} > {MAX_TICKERS}); screening is "
            "bounded candidate triage, not a market crawl — narrow the universe first")

    market = _resolve_market(symbols)
    registry = CRITERIA_CN if market == "CN" else CRITERIA
    unknown = sorted(set(criteria) - set(registry))
    if unknown:
        market_note = (" (A-share criteria)" if market == "CN" else "")
        raise ValueError(f"unknown screening criteria{market_note}: {unknown}; "
                         f"this market supports {sorted(registry)}")
    if market_scope and market_scope.upper() != market:
        raise ValueError(
            f"market_scope={market_scope!r} contradicts the candidate list, which is {market}")

    as_of = as_of or _dt.date.today().isoformat()
    rows: list[dict] = []
    derived: list[CanonicalRecord] = []

    if market == "CN":
        for i, sym in enumerate(symbols):
            if i:
                _pause()
            row, recs = _screen_one_cn(sym, criteria, as_of)
            rows.append(row)
            derived.extend(recs)
    else:
        scf._require_contact()
        cik_map = _cik_map(symbols)
        for i, sym in enumerate(symbols):
            if i:
                _pause()
            row, recs = _screen_one(sym, cik_map.get(sym), criteria, as_of, "US")
            rows.append(row)
            derived.extend(recs)

    # `partial` counts as evaluated: the candidate WAS judged, on the criteria that have data.
    # Treating it as a failure here would reject a legitimate all-partial batch, which is the same
    # mistake as hard-failing an unavailable criterion one level down.
    if not any(r["screen_status"] in ("pass", "partial", "fail") for r in rows):
        raise net.FetchError(
            "source_gap: no candidate could be evaluated (all fetches failed or gated)")

    counts = {s: sum(1 for r in rows if r["screen_status"] == s)
              for s in ("pass", "partial", "fail", "data_gap")}
    summary = {"as_of": as_of, "criteria": _criteria_label(criteria), "market": market,
               "n_candidates": len(rows), **counts}
    return ScreenResult(rows=rows, derived=derived, summary=summary)


def _resolve_market(symbols: list) -> str:
    """Decide the batch's market, refusing a mixed one.

    The refusal is the point: one numeric threshold cannot mean both USD and CNY, so a mixed
    batch would apply thresholds roughly 7x apart under a single predicate. Silently converting
    would import an FX assumption this substrate does not otherwise make.
    """
    cn = [s for s in symbols if cn_symbols.is_bare_a_share(s)]
    if cn and len(cn) != len(symbols):
        us = [s for s in symbols if s not in cn]
        raise ValueError(
            "cannot screen multi-currency tickers in a single batch without explicit "
            f"conversion: A-share {cn[:4]} and non-A-share {us[:4]} were mixed, and the same "
            "threshold would mean CNY for one and USD for the other. Run one market per batch")
    return "CN" if cn else "US"


# --- per-ticker evaluation ---------------------------------------------------

def _screen_one(sym: str, cik: Optional[str], criteria: dict, as_of: str,
                market_scope: str) -> tuple[dict, list]:
    if cik is None:
        return _row(sym, as_of, market_scope, "data_gap", criteria, {}, basis="",
                    price_as_of="source_gap", gaps=["sec_ticker_map"],
                    notes="not in SEC company_tickers.json (US registrants only)"), []
    try:
        _, facts, _url = scf.load_facts(sym, cik=cik)
    except net.FetchError as exc:
        return _row(sym, as_of, market_scope, "data_gap", criteria, {}, basis="",
                    price_as_of="source_gap", gaps=["companyfacts_fetch"],
                    notes=str(exc)[:160]), []

    obs = {m: scf.metric_observations(facts, m)
           for m in ("revenue", "net_income", "operating_cash_flow", "capex",
                     "long_term_debt", "stockholders_equity", "shares_outstanding")}

    gaps: list[str] = []
    price = price_date = None
    try:
        price, price_date = _last_close(sym)
    except net.FetchError:
        gaps.append("market_price")

    metrics: dict[str, float] = {}
    formulas: dict[str, tuple[str, str]] = {}  # metric -> (formula, upstream_kind)
    basis_bits: list[str] = []

    shares = _latest_instant(obs["shares_outstanding"])
    if price is not None and shares is not None and _fresh(shares, as_of):
        metrics["market_cap_usd"] = price * shares["val"]
        formulas["market_cap_usd"] = (
            f"last_close({price_date}) * shares_outstanding({shares['end']})", "sec+yahoo")

    fy, ocf, capex = _common_annual(obs["operating_cash_flow"], obs["capex"])
    cash_flow_period_end = "source_gap"
    if (ocf is not None and capex is not None and metrics.get("market_cap_usd")
            and _fresh(ocf, as_of)):
        metrics["fcf_yield"] = (ocf["val"] - capex["val"]) / metrics["market_cap_usd"]
        formulas["fcf_yield"] = (
            f"(operating_cash_flow(FY{fy}) - capex(FY{fy})) / market_cap_usd", "sec+yahoo")
        basis_bits.append(f"flows FY{fy}")
        cash_flow_period_end = ocf.get("end") or "source_gap"

    debt = _latest_instant(obs["long_term_debt"])
    equity = _latest_instant(obs["stockholders_equity"])
    if (debt is not None and equity is not None and equity["val"]
            and _fresh(debt, as_of) and _fresh(equity, as_of)):
        metrics["debt_to_equity"] = debt["val"] / equity["val"]
        formulas["debt_to_equity"] = (
            f"long_term_debt({debt['end']}) / stockholders_equity({equity['end']})", "sec")
        basis_bits.append(f"balance {equity['end']}")

    fy_m, ni, rev = _common_annual(obs["net_income"], obs["revenue"])
    if ni is not None and rev is not None and rev["val"] and _fresh(rev, as_of):
        metrics["net_margin"] = ni["val"] / rev["val"]
        formulas["net_margin"] = (f"net_income(FY{fy_m}) / revenue(FY{fy_m})", "sec")

    yoy = _yoy(obs["revenue"])
    revenue_yoy_period = "source_gap"
    if yoy is not None and _fresh(yoy[1], as_of):
        value, latest, base = yoy
        metrics["revenue_yoy"] = value
        formulas["revenue_yoy"] = (
            f"revenue(FY{latest.get('fy')} {latest.get('fp')}) / "
            f"revenue(FY{base.get('fy')} {base.get('fp')}) - 1", "sec")
        revenue_yoy_period = (f"FY{latest.get('fy')} {latest.get('fp')} "
                              f"vs FY{base.get('fy')} {base.get('fp')}")
        basis_bits.append(f"yoy {revenue_yoy_period}")

    if price_date:
        basis_bits.append(f"price {price_date}")

    status, gap_criteria = _evaluate(criteria, metrics)
    gaps.extend(gap_criteria)

    row = _row(sym, as_of, market_scope, status, criteria, metrics,
               basis="; ".join(basis_bits) or "source_gap",
               price_as_of=price_date or "source_gap", gaps=gaps,
               cash_flow_period_end=cash_flow_period_end,
               revenue_yoy_period=revenue_yoy_period)
    recs = _derived_records(sym, as_of, market_scope, metrics, formulas) if status == "pass" else []
    return row, recs


def _screen_one_cn(sym: str, criteria: dict, as_of: str) -> tuple[dict, list]:
    """A-share path: vendor standardised statements + the vendor's indicator set.

    Kept entirely separate from the SEC path so US behaviour cannot change as a side effect of
    adding a market. Where a metric is unavailable by construction it becomes a Partial gap that
    is reported and does not by itself fail the candidate.
    """
    try:
        gathered = hscr.fetch_screen_metrics(sym, as_of=as_of)
    except net.FetchError as exc:
        return _row(sym, as_of, "CN", "data_gap", criteria, {}, basis="",
                    price_as_of="source_gap", gaps=["financials_fetch"],
                    notes=str(exc)[:160]), []

    metrics = dict(gathered["metrics"])
    periods = gathered["periods"]
    formulas = gathered["formulas"]
    caveats = gathered["caveats"]
    gaps = [g for g in gathered["gaps"] if g not in hscr.UNAVAILABLE_METRICS]

    # The A-share row also carries the last close, purely as context: it is NOT used to derive a
    # market cap, which is exactly the fabrication this path refuses.
    price = price_date = None
    try:
        price, price_date = _last_close(sym)
    except net.FetchError:
        gaps.append("market_price")

    status, gap_criteria = _evaluate_cn(criteria, metrics)
    gaps.extend(gap_criteria)

    basis_bits = []
    if periods.get("net_margin"):
        basis_bits.append(f"flows {periods['net_margin']}")
    if periods.get("revenue_yoy"):
        basis_bits.append(f"yoy {periods['revenue_yoy']}")
    if periods.get("debt_to_equity"):
        basis_bits.append(f"leverage {periods['debt_to_equity']}")
    if price_date:
        basis_bits.append(f"price {price_date}")

    row = _row(sym, as_of, "CN", status, criteria, metrics,
               basis="; ".join(basis_bits) or "source_gap",
               price_as_of=price_date or "source_gap", gaps=gaps,
               cash_flow_period_end=periods.get("net_margin", "source_gap"),
               revenue_yoy_period=periods.get("revenue_yoy", "source_gap"),
               registry=CRITERIA_CN)
    row["assets_debt_ratio"] = (round(metrics["assets_debt_ratio"], 6)
                               if "assets_debt_ratio" in metrics else "source_gap")
    row["leverage_caveat"] = caveats.get("debt_to_equity", "")
    # The internal metric is `market_cap`; the CSV column names its currency, because A-share
    # market cap is CNY and must not share a header asserting USD. It stays source_gap until a
    # channel exposes shares outstanding (see hithink_screen.UNAVAILABLE_METRICS).
    row["market_cap_cny"] = (round(metrics["market_cap"], 6)
                             if "market_cap" in metrics else "source_gap")
    row["upstream_sources"] = f"hithink_finance_financials_api:{sym}"
    row["evidence_tier_note"] = _TIER_NOTE_CN
    row["notes"] = _cn_notes(gathered, criteria)

    recs = (_derived_records_cn(sym, as_of, metrics, formulas, periods)
            if status in ("pass", "partial") else [])
    return row, recs


def _evaluate_cn(criteria: dict, metrics: dict) -> tuple[str, list]:
    """Like :func:`_evaluate` but a criterion with NO data source is not a failure.

    ``min_market_cap`` and ``min_fcf_yield`` cannot be computed for A-shares at all. Hard-failing
    on them would reject every A-share on a criterion that is unavailable by construction, which
    is the original bug in a new costume; silently skipping them would drop the user's stated
    intent. So they are neither: the candidate is judged on the criteria that do have data, and
    the unavailable one is named in the row's gaps.
    """
    failed, gap = [], []
    unavailable = []
    for name, threshold in criteria.items():
        metric, direction = CRITERIA_CN[name]
        value = metrics.get(metric)
        if value is None:
            (unavailable if metric in hscr.UNAVAILABLE_METRICS else gap).append(metric)
            continue
        ok = value >= threshold if direction == "min" else value <= threshold
        if not ok:
            failed.append(name)
    if failed:
        return "fail", gap + unavailable
    if gap:
        return "data_gap", gap + unavailable
    if unavailable:
        return "partial", unavailable
    return "pass", []


def _cn_notes(gathered: dict, criteria: dict) -> str:
    """Spell out units, currency and the partial-gap reasoning on the row itself."""
    bits = [f"basis {gathered['report']}", "money CNY"]
    # The metrics the caller asked for that this market simply cannot produce. Named on the row so
    # a reader can tell "the screen passed" from "the screen passed on the criteria that exist".
    requested_unavailable = sorted({
        CRITERIA_CN[name][0] for name in criteria
        if CRITERIA_CN[name][0] in hscr.UNAVAILABLE_METRICS
    })
    if requested_unavailable:
        bits.append("PARTIAL: " + "; ".join(
            f"{metric} unavailable - {hscr.UNAVAILABLE_METRICS[metric]}"
            for metric in requested_unavailable))
    bits.append("leverage = vendor long_term_debt_equity_ratio, not total liabilities")
    return " | ".join(bits)


def _derived_records_cn(sym, as_of, metrics, formulas, periods) -> list:
    """Ledgered L6 records for an A-share candidate that passed (wholly or partially)."""
    out = []
    for metric, value in sorted(metrics.items()):
        if metric not in formulas:
            continue
        caveat = ""
        if metric in ("debt_to_equity", "assets_debt_ratio"):
            caveat = hscr.VENDOR_PUBLISHED_CAVEAT
        out.append(CanonicalRecord(
            family="company_financials", research_object=sym, market_scope="CN",
            metric=metric, value=round(float(value), 6),
            unit="ratio", period=periods.get(metric, as_of), period_type="point_in_time",
            as_of_date=as_of, source_date=as_of,
            posture=POSTURES["hithink_finance_financials"],
            url_or_path="derived://tools/mira_data/screening",
            derived=True,
            upstream_sources=f"hithink_finance_financials_api:{sym}",
            formula=formulas[metric],
            cross_check=caveat or "screening_grade; verify vs filings before durable use",
            claim_text=f"{sym} {metric} = {round(float(value), 6)} (A-share screen, {as_of})",
        ))
    return out


def _evaluate(criteria: dict, metrics: dict) -> tuple[str, list]:
    failed, gap = [], []
    for name, threshold in criteria.items():
        metric, direction = CRITERIA[name]
        value = metrics.get(metric)
        if value is None:
            gap.append(metric)
            continue
        ok = value >= threshold if direction == "min" else value <= threshold
        if not ok:
            failed.append(name)
    if failed:
        return "fail", gap
    if gap:
        return "data_gap", gap
    return "pass", gap


def _row(sym, as_of, market_scope, status, criteria, metrics, *, basis,
         price_as_of, gaps, notes="", cash_flow_period_end="source_gap",
         revenue_yoy_period="source_gap", registry=None) -> dict:
    row = {
        "screened_date": as_of,
        "ticker": sym,
        "market_scope": market_scope,
        "screen_status": status,
        "criteria": _criteria_label(criteria),
        "fundamentals_basis": basis,
        # Period basis per metric family: a row mixes annual flows (fcf_yield,
        # net_margin) with a quarterly YoY, so each basis is named explicitly
        # instead of hiding the mix behind one "fundamentals" label.
        "cash_flow_period_end": cash_flow_period_end,
        "revenue_yoy_period": revenue_yoy_period,
        "price_as_of": price_as_of,
        "data_gaps": ";".join(dict.fromkeys(gaps)) or "none",
        "upstream_sources": f"sec_companyfacts_api:{sym};yahoo_chart_api_v8:{sym}",
        "evidence_tier_note": _TIER_NOTE,
        "next_action": _NEXT_ACTION[status],
        "notes": notes,
    }
    columns = _METRIC_COLUMNS + (_CN_EXTRA_COLUMNS if registry is CRITERIA_CN else ())
    for col in columns:
        value = metrics.get(col)
        row[col] = round(value, 6) if isinstance(value, float) else (
            value if value is not None else "source_gap")
    return row


def _derived_records(sym, as_of, market_scope, metrics, formulas) -> list:
    base = POSTURES["sec_companyfacts"]
    upstream = {
        "sec": f"sec_companyfacts_api:{sym}",
        "sec+yahoo": f"sec_companyfacts_api:{sym};yahoo_chart_api_v8:{sym}",
    }
    family = {"market_cap_usd": "valuation_snapshot", "fcf_yield": "valuation_snapshot"}
    out = []
    for metric in _METRIC_COLUMNS:
        value = metrics.get(metric)
        if value is None or metric not in formulas:
            continue
        formula, kind = formulas[metric]
        out.append(CanonicalRecord(
            family=family.get(metric, "company_financials"),
            research_object=sym, market_scope=market_scope,
            metric=metric, value=round(value, 6),
            unit="USD" if metric == "market_cap_usd" else "ratio",
            period=as_of, period_type="point_in_time", as_of_date=as_of,
            source_date=as_of, posture=base,
            url_or_path="derived://tools/mira_data/screening",
            derived=True, upstream_sources=upstream[kind], formula=formula,
            cross_check="screening_grade; verify vs filings before durable use",
            claim_text=f"{sym} {metric} = {round(value, 6)} (screen, {as_of})",
        ))
    return out


# --- observation selection ---------------------------------------------------

def _annual_by_fy(obs: list) -> dict:
    """Latest filed annual (~12-month span) observation per fiscal year."""
    by_fy = {}
    for r in obs:  # sorted asc by (end, filed): latest filing for the fy wins
        span = r.get("_span")
        if span is not None and scf._is_annual(span) and r.get("fy") is not None and r.get("val") is not None:
            by_fy[r["fy"]] = r
    return by_fy


def _common_annual(obs_a: list, obs_b: list):
    """Latest fiscal year where both metrics have an annual value."""
    a, b = _annual_by_fy(obs_a), _annual_by_fy(obs_b)
    common = sorted(set(a) & set(b))
    if not common:
        return None, None, None
    fy = common[-1]
    return fy, a[fy], b[fy]


def _latest_instant(obs: list) -> Optional[dict]:
    rows = [r for r in obs if r.get("_span") is None and r.get("val") is not None]
    return rows[-1] if rows else None


def _fresh(row: dict, as_of: str) -> bool:
    """True when the observation's period end is recent enough to screen on."""
    try:
        end = _dt.date.fromisoformat(row.get("end", ""))
        return (_dt.date.fromisoformat(as_of) - end).days <= _STALE_DAYS
    except ValueError:
        return False


def _last_close(sym: str) -> tuple[float, str]:
    res = yahoo_chart.fetch_market_price(sym, range_="1mo")
    rows = res.series["rows"]
    for rec in res.records:
        if rec.metric == "last_close" and rec.value is not None:
            return float(rec.value), rec.source_date
    return float(rows[-1]["close"]), rows[-1]["date"]


# --- helpers -----------------------------------------------------------------

def _cik_map(symbols: list) -> dict:
    """One ticker-map fetch resolves every candidate (vs one fetch per ticker)."""
    data = scf.load_ticker_cik_map()
    want = set(symbols)
    return {ticker: cik for ticker, cik in data.items() if ticker in want}


def _criteria_label(criteria: dict) -> str:
    return ";".join(f"{k}={v:g}" for k, v in sorted(criteria.items()))


def _pause() -> None:
    # Wrapped so tests can monkeypatch; keeps the run inside polite rate limits.
    time.sleep(_PAUSE_SECONDS)
