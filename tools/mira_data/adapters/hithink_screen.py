"""A-share screening metrics -> the market-neutral screening layer (L5 vendor, L6 derived).

This is the A-share data source for ``screen``. The US path reads SEC companyfacts; this one reads
the vendor's standardised statements and its pre-computed indicator set, and it exists because
``screen`` previously returned ``data_gap`` for every A-share before computing a single metric —
not because the data was missing but because the code path was SEC-only.

**Four of the five original criteria are computable, and the reasons each one is trustworthy (or
not) are the substance of this module.**

1. **``debt_to_equity`` — the vendor's ``long_term_debt_equity_ratio``, NOT total liabilities.**
   This is the point of the module. The vendor's balance sheet exposes a field literally named
   ``total_debt``, and it is a trap: it equals ``assets_total - holder_equity_total`` to the penny
   (600183: 13,859,467,654.75 both ways), so it is the whole liability side renamed. Feeding it to
   ``--max-debt-to-equity`` would filter out asset-light, high-bargaining-power businesses whose
   liabilities are nearly all interest-free payables — the exact failure the criterion exists to
   avoid. The indicator set instead publishes ``long_term_debt_equity_ratio`` (600183: 19.93%),
   which matches the US semantic. It also publishes ``assets_debt_ratio`` (50.33% = the
   conventional 资产负债率), offered as a separate A-share-native criterion.
   **Caveat recorded, not hidden:** the long-term figure cannot be independently recomputed,
   because the balance-sheet channel exposes no borrowing line items (no short-term borrowings, no
   long-term borrowings, no bonds payable). It is therefore recorded as a *vendor-published value*
   rather than a Mira-derived one.

2. **``net_margin`` is recomputed, not taken.** ``net_profit / operating_income`` on the standard
   income statement reproduces the vendor's own ``sale_net_interest_ratio`` exactly when the
   periods match (2026-Q2: 19.5412% both ways), so Mira's formula is used and ledgered while the
   vendor's figure serves as the cross-check. Note the denominator is **operating income
   (营业收入)**, not operating profit — a detail worth stating because the English field name
   invites the wrong reading.

3. **``revenue_yoy`` is computed from ANNUAL rows only, and this is a deliberate restriction.**
   The vendor's ``--period quarterly`` income series returns **cumulative year-to-date** figures,
   not single quarters: 2025 reads Q1 5.61bn, Q2 12.68bn, Q3 20.61bn, Q4 28.43bn, where the Q4
   value equals the annual figure exactly. So "2026-Q2" is H1 cumulative. Same-quarter cumulative
   comparison is valid, but a quarter-on-quarter subtraction would manufacture an apparent +134%
   growth from the accumulation itself. Annual rows carry no such ambiguity, so v1 uses them, and
   the metric is named ``revenue_yoy`` with the period basis recorded alongside.

4. **``market_cap`` and ``fcf_yield`` are Partial gaps for A-shares, by evidence rather than by
   convenience.** No A-share channel exposes shares outstanding or total market value: the
   valuation snapshot carries only PE/PB/PS/PCF, the market snapshot carries only price and
   turnover, and the indicator set carries no share count. Deriving a market cap from
   ``price x shares`` is what this refusal is about — and it is not merely a staleness risk, it is
   demonstrably unreliable: backing shares out of the vendor's own ratios gives 18.20bn from
   ``pe_ttm`` but 14.38bn from ``pe_mrq``, a 27% disagreement, because the two ratios use
   different earnings windows. Since ``fcf_yield`` needs that denominator, it is a gap too. Both
   are reported as Partial (other criteria still decide), never silently skipped and never
   estimated.

Units: all money is **CNY**. No currency conversion is performed anywhere in this module.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional

from .. import cn_symbols, net
from .hithink_finance import vendor_json

# The vendor splits what the US path reads from one source across three commands, so each metric
# records which command produced it.
STATEMENTS_CMD = "financials.income"
BALANCE_CMD = "financials.balance-sheet"
INDICATORS_CMD = "financials.indicators"

# Metric -> the vendor indicator name, for the ones taken as published.
INDICATOR_METRICS = {
    "debt_to_equity": "long_term_debt_equity_ratio",
    "assets_debt_ratio": "assets_debt_ratio",
}

# A criterion we cannot compute for A-shares must be a PARTIAL gap: the candidate is still judged
# on the criteria we do have, and the missing one is named. Hard-failing here would restore the
# original bug in a new costume.
UNAVAILABLE_METRICS = {
    "market_cap": ("no A-share channel exposes shares outstanding or total market value, and a "
                   "derived share count is unreliable (pe_ttm and pe_mrq imply counts 27% apart)"),
    "fcf_yield": ("needs a market-cap denominator, which is unavailable for the same reason"),
}

ANNUAL_ROWS = 3          # enough for one YoY pair plus a spare

# Recorded on every leverage metric taken from the vendor's indicator set. The value is the right
# SEMANTIC (long-term debt, not total liabilities) but its inputs are not exposed by any channel -
# the balance sheet offers no borrowing line items at all - so it cannot be independently
# recomputed. Saying so is the difference between a sourced figure and an assumed one. Observed
# trend for 600183, which is at least consistent with a leverage ratio: FY2024 8.65%, FY2025
# 10.64%, 2026-Q2 19.93%.
VENDOR_PUBLISHED_CAVEAT = (
    "vendor-published indicator, taken as-is: the semantic is long-term debt / equity (not total "
    "liabilities, which is what the balance sheet's deceptively-named total_debt field holds), but "
    "the vendor exposes no borrowing line items, so Mira cannot independently recompute it")


def fetch_screen_metrics(code: str, *, as_of: Optional[str] = None) -> dict:
    """Gather the screening metrics for one A-share code.

    Returns ``{"metrics": {...}, "periods": {...}, "formulas": {...}, "gaps": [...]}``. Missing
    metrics are simply absent from ``metrics`` so the caller can classify them as Partial gaps
    rather than failures.
    """
    as_of = as_of or _dt.date.today().isoformat()
    thscode = _thscode(code)
    gaps: list[str] = []
    metrics: dict[str, float] = {}
    periods: dict[str, str] = {}
    formulas: dict[str, str] = {}
    published: dict[str, float] = {}      # the vendor's own (percent) value, for auditability
    caveats: dict[str, str] = {}

    # --- annual income, for the margin and the YoY -------------------------------------------
    income = _rows(vendor_json(["financials", "income", "--thscode", thscode,
                               "--period", "annual", "--limit", str(ANNUAL_ROWS)]))
    latest = income[0] if income else {}
    prior = income[1] if len(income) > 1 else {}
    # The annual income command names revenue `revenue`; the quarterly one omits it, which is a
    # second reason v1 stays on annual rows.
    revenue = _num(latest.get("revenue")) or _num(latest.get("operating_income"))
    net_profit = _num(latest.get("net_income")) or _num(latest.get("net_profit"))
    if revenue and net_profit is not None:
        metrics["net_margin"] = net_profit / revenue
        periods["net_margin"] = _label(latest)
        formulas["net_margin"] = (f"net_income({_label(latest)}) / revenue({_label(latest)})")
    else:
        gaps.append("net_margin")

    prior_revenue = _num(prior.get("revenue")) or _num(prior.get("operating_income"))
    if revenue and prior_revenue:
        metrics["revenue_yoy"] = revenue / prior_revenue - 1
        periods["revenue_yoy"] = f"{_label(latest)} vs {_label(prior)} (annual, not cumulative)"
        formulas["revenue_yoy"] = (f"revenue({_label(latest)}) / revenue({_label(prior)}) - 1")
    else:
        gaps.append("revenue_yoy")

    # --- balance sheet, only for the equity needed by the margins above ----------------------
    balance = _rows(vendor_json(["financials", "balance-sheet", "--thscode", thscode,
                                 "--period", "annual", "--limit", "1"]))
    equity = _num((balance[0] if balance else {}).get("holder_equity_total"))
    if equity:
        periods["equity"] = _label(balance[0]) if balance else "source_gap"
    else:
        gaps.append("equity")

    # --- indicators, for the leverage figures taken as published ------------------------------
    report = _latest_report_period(latest) or _guess_report(as_of)
    indicators = _indicator_map(thscode, report)
    for metric, index_id in INDICATOR_METRICS.items():
        raw = _num(indicators.get(index_id))
        if raw is None:
            gaps.append(metric)
            continue
        # UNIT CONVERSION, and it matters: the indicator set publishes these as PERCENTS
        # (assets_debt_ratio 42.2937 means 42.29%) while every ratio Mira computes is a plain
        # ratio (net_margin 0.136888). Mixing the two scales would make the same threshold mean
        # different things per metric, so the published percent is divided by 100 here and both
        # the raw and converted values are recorded.
        metrics[metric] = raw / 100.0
        periods[metric] = report
        formulas[metric] = f"vendor indicator {index_id} ({report}), published not recomputed"
        published[metric] = raw
        caveats[metric] = VENDOR_PUBLISHED_CAVEAT

    # --- metrics that are genuinely unavailable, named so the caller can report them ----------
    for metric in UNAVAILABLE_METRICS:
        gaps.append(metric)

    return {"metrics": metrics, "periods": periods, "formulas": formulas, "gaps": gaps,
            "published_percent": published, "caveats": caveats,
            "as_of": as_of, "thscode": thscode, "report": report}


def _indicator_map(thscode: str, report: str) -> dict:
    """Flatten the indicator payload (grouped by ``abilities``) into ``{index_id: value}``."""
    data = vendor_json(["financials", "indicators", "--thscode", thscode, "--report", report])
    out: dict[str, object] = {}
    for group in (data.get("abilities") or []):
        for item in (group.get("indicators") or []):
            index_id = item.get("index_id")
            if index_id:
                out[str(index_id)] = item.get("value")
    return out


def _rows(data) -> list[dict]:
    items = data.get("item") if isinstance(data, dict) else data
    return [r for r in (items or []) if isinstance(r, dict)]


def _thscode(code: str) -> str:
    """``600183`` -> ``600183.SH``; a suffixed or thscode input passes through."""
    text = str(code or "").strip().upper()
    if "." in text:
        return text
    exchange = cn_symbols.exchange_of(text)
    return f"{text}.{exchange}" if exchange else cn_symbols.to_yahoo_symbol(text).replace(".SS", ".SH")


def _label(row: dict) -> str:
    year = row.get("fiscal_year")
    period = row.get("fiscal_period")
    return f"FY{year}" if year else str(period or "source_gap")


def _latest_report_period(income_row: dict) -> Optional[str]:
    """``FY2025`` -> ``2025-4``; the indicators command wants ``YYYY-[1-4]``."""
    year = income_row.get("fiscal_year")
    return f"{year}-4" if year else None


def _guess_report(as_of: str) -> str:
    year, month = int(as_of[:4]), int(as_of[5:7])
    quarter = max(1, min(4, (month - 1) // 3))
    return f"{year}-{quarter}"


def _num(value):
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None
