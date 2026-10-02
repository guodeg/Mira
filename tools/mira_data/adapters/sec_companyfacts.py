"""SEC EDGAR companyfacts adapter -> canonical ``company_financials``.

Official, keyless (a descriptive User-Agent is required). Values are
issuer-disclosed reported metrics: ``derived=False``, so no calculation ledger
is required (arch doc §8). Mira-computed ratios/deltas are a separate, later
concern that must be emitted as ``derived_calculation`` rows.
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .. import config, net
from ..canonical import POSTURES, CanonicalRecord, FetchResult

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
TICKER_EXCHANGE_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
SEC_HEADERS = {"Accept-Encoding": "identity"}
SEC_MAP_RETRIES = 5
SEC_MAP_BACKOFF = 0.5
SEC_FACT_RETRIES = 4
SEC_FACT_BACKOFF = 0.5
_REPO_ROOT = Path(__file__).resolve().parents[3]
_TICKER_CACHE_PATH = _REPO_ROOT / "local" / "mira-data-cache" / "sec-ticker-cik-map.json"
_TICKER_CACHE_MAX_AGE = _dt.timedelta(days=7)

# Cash-flow tags are the ones issuers file cumulatively: a Q2/Q3 10-Q carries the
# 6-/9-month year-to-date figure, not a clean quarter. They are therefore the
# metrics the ``cash_flow_span`` option applies to.
CASH_FLOW_METRICS = frozenset({"operating_cash_flow", "capex"})
CASH_FLOW_SPANS = ("quarter", "ytd")


def _require_contact() -> None:
    """SEC requires a real contact User-Agent; refuse to fetch under a placeholder."""
    if not config.is_contact_configured():
        raise net.FetchError(
            "SEC official-data fetch needs a real contact. " + config.config_hint()
        )

# Curated snapshot tags: (canonical_metric, taxonomy, kind, [candidate tags]).
# kind drives period selection: "instant" = balance-sheet point-in-time (pick
# latest end); "duration" = flow metric (pick latest clean QUARTER, else annual,
# explicitly skipping 6-/9-month YTD rows that share the same tag).
# Among present candidate tags, the one with the most recent observation wins,
# so issuer tag drift is tolerated and an abandoned tag never shadows the live one.
CURATED_TAGS: list[tuple[str, str, str, list[str]]] = [
    ("revenue", "us-gaap", "duration", ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"]),
    ("gross_profit", "us-gaap", "duration", ["GrossProfit"]),
    ("operating_income", "us-gaap", "duration", ["OperatingIncomeLoss"]),
    ("net_income", "us-gaap", "duration", ["NetIncomeLoss"]),
    ("rnd_expense", "us-gaap", "duration", ["ResearchAndDevelopmentExpense"]),
    ("operating_cash_flow", "us-gaap", "duration", ["NetCashProvidedByUsedInOperatingActivities"]),
    ("capex", "us-gaap", "duration", ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"]),
    ("total_assets", "us-gaap", "instant", ["Assets"]),
    ("total_liabilities", "us-gaap", "instant", ["Liabilities"]),
    ("stockholders_equity", "us-gaap", "instant", ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]),
    ("cash_and_equivalents", "us-gaap", "instant", ["CashAndCashEquivalentsAtCarryingValue"]),
    ("long_term_debt", "us-gaap", "instant", ["LongTermDebtNoncurrent", "LongTermDebt"]),
    ("shares_outstanding", "dei", "instant", ["EntityCommonStockSharesOutstanding"]),
]


def resolve_cik(ticker: str) -> str:
    """Return the 10-digit zero-padded CIK for ``ticker`` (case-insensitive)."""
    want = ticker.strip().upper()
    cik = load_ticker_cik_map().get(want)
    if cik:
        return cik
    raise net.FetchError(f"ticker not found in SEC company_tickers.json: {ticker}")


def load_ticker_cik_map() -> dict[str, str]:
    """Load the SEC ticker map, preferring the smaller exchange payload."""
    _require_contact()
    cached = _read_ticker_cache()
    if cached:
        return cached

    errors = []
    for url in (TICKER_EXCHANGE_URL, TICKER_MAP_URL):
        try:
            result = _parse_ticker_cik_map(
                net.get_json(
                    url,
                    headers=SEC_HEADERS,
                    retries=SEC_MAP_RETRIES,
                    backoff=SEC_MAP_BACKOFF,
                )
            )
        except net.FetchError as exc:
            errors.append(f"{url}: {exc}")
            continue
        if result:
            _write_ticker_cache(result)
            return result
        errors.append(f"{url}: no ticker rows")
    raise net.FetchError("SEC ticker-map fetch failed; " + "; ".join(errors))


def _read_ticker_cache() -> dict[str, str]:
    try:
        modified = _dt.datetime.fromtimestamp(_TICKER_CACHE_PATH.stat().st_mtime)
        if _dt.datetime.now() - modified > _TICKER_CACHE_MAX_AGE:
            return {}
        payload = json.loads(_TICKER_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return _normalize_ticker_cik_map(payload)


def _write_ticker_cache(payload: dict[str, str]) -> None:
    try:
        _TICKER_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = _TICKER_CACHE_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        temporary.replace(_TICKER_CACHE_PATH)
    except OSError:
        # The cache is an optimization; a read-only checkout must still work.
        return


def _parse_ticker_cik_map(payload: dict) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {}
    if isinstance(payload.get("fields"), list) and isinstance(payload.get("data"), list):
        fields = payload["fields"]
        try:
            ticker_idx = fields.index("ticker")
            cik_idx = fields.index("cik")
        except ValueError:
            return {}
        return _normalize_ticker_cik_map(
            {
                row[ticker_idx]: row[cik_idx]
                for row in payload["data"]
                if len(row) > max(ticker_idx, cik_idx)
            }
        )

    return _normalize_ticker_cik_map(
        {
            row.get("ticker"): row.get("cik_str")
            for row in payload.values()
            if isinstance(row, dict)
        }
    )


def _normalize_ticker_cik_map(payload: object) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {}
    normalized: dict[str, str] = {}
    for ticker, cik in payload.items():
        ticker_text = str(ticker).strip().upper()
        if not ticker_text:
            continue
        try:
            cik_text = f"{int(cik):010d}"
        except (TypeError, ValueError):
            continue
        normalized[ticker_text] = cik_text
    return normalized


def load_facts(ticker: str, cik: Optional[str] = None) -> tuple[str, dict, str]:
    """Fetch raw companyfacts: returns ``(cik10, facts, url)``. Gated on contact."""
    _require_contact()
    cik10 = cik or resolve_cik(ticker)
    url = COMPANYFACTS_URL.format(cik10=cik10)
    facts = net.get_json(
        url,
        headers=SEC_HEADERS,
        retries=SEC_FACT_RETRIES,
        backoff=SEC_FACT_BACKOFF,
    ).get("facts", {})
    return cik10, facts, url


def metric_observations(facts: dict, metric: str) -> list[dict]:
    """All observations for a curated metric, sorted by period end then filed.

    Each obs carries ``_unit``, ``_span`` (days, or None for instant) and ``_tag``.
    Lets callers build YoY / QoQ / CAGR from a real series, not just the latest.
    """
    spec = next((s for s in CURATED_TAGS if s[0] == metric), None)
    if spec is None:
        return []
    _, taxonomy, _kind, candidates = spec
    block = _first_present(facts.get(taxonomy, {}), candidates)
    if block is None:
        return []
    obs = []
    for unit, rows in block["units"].items():
        for row in rows:
            if "end" not in row or "val" not in row:
                continue
            obs.append({**row, "_unit": unit, "_span": _span_days(row.get("start"), row.get("end")),
                        "_tag": block["_tag"]})
    return sorted(obs, key=lambda r: (r.get("end", ""), r.get("filed", "")))


def fetch_company_financials(
    ticker: str,
    *,
    as_of: Optional[str] = None,
    market_scope: str = "US",
    cik: Optional[str] = None,
    cash_flow_span: str = "quarter",
) -> FetchResult:
    """Fetch a curated financial snapshot as canonical reported metrics.

    ``cash_flow_span`` controls the cash-flow metrics only (``operating_cash_flow``,
    ``capex``), because those are the tags issuers file cumulatively:

    - ``"quarter"`` (default): latest clean single-quarter span. Issuers that only
      file Q2/Q3 cash flow as 6-/9-month YTD leave this at the last clean quarter,
      so it can lag the income statement by two quarters — the mix is recorded in
      each claim's ``period`` and ``conversion`` provenance, never hidden.
    - ``"ytd"``: use the newest cumulative fiscal-year-to-date filing, reconciled to
      a single quarter by subtracting the previous cumulative observation of the
      same fiscal year (``9M - 6M``). That delta is Mira-computed, so it is emitted
      as a ledgered ``derived_calculation``. When no prior cumulative observation
      exists the value is reported as filed with an explicit ``{n}M`` period label.
    """
    if cash_flow_span not in CASH_FLOW_SPANS:
        raise ValueError(f"unknown cash_flow_span {cash_flow_span!r}; use one of {CASH_FLOW_SPANS}")
    as_of = as_of or _dt.date.today().isoformat()
    cik10, facts, url = load_facts(ticker, cik)
    posture = POSTURES["sec_companyfacts"]

    records: list[CanonicalRecord] = []
    for metric, taxonomy, kind, candidates in CURATED_TAGS:
        tagblock = _first_present(facts.get(taxonomy, {}), candidates)
        if tagblock is None:
            continue
        selection = _select_metric_observation(
            tagblock, kind, metric, cash_flow_span=cash_flow_span)
        if selection is None:
            continue
        obs = selection.obs
        records.append(
            CanonicalRecord(
                family="company_financials",
                research_object=ticker.upper(),
                market_scope=market_scope,
                metric=metric,
                value=obs["val"],
                unit=selection.unit,
                currency="USD" if selection.unit == "USD" else None,
                period=selection.period,
                period_type="fiscal_period",
                as_of_date=as_of,
                source_date=obs.get("filed", as_of),
                posture=posture,
                url_or_path=url,
                derived=selection.derived,
                upstream_sources=f"{posture.source_id}:{ticker.upper()}" if selection.derived else "",
                formula=selection.formula,
                cross_check=("cumulative companyfacts rows differenced within one fiscal year"
                             if selection.derived else ""),
                provenance={
                    "cik": cik10,
                    "tag": obs["_tag"],
                    "taxonomy": taxonomy,
                    "fy": obs.get("fy"),
                    "fp": obs.get("fp"),
                    "form": obs.get("form"),
                    "accn": obs.get("accn"),
                    "period_end": obs.get("end"),
                    "period_start": obs.get("start"),
                    "span_days": selection.span_days,
                    "conversion": selection.conversion,
                    "prior_period_end": selection.prior_end,
                },
            )
        )
    if not records:
        raise net.FetchError(f"no curated facts extracted for {ticker} ({cik10})")
    return FetchResult(records)


@dataclass(frozen=True)
class Selection:
    """How one curated metric was resolved from companyfacts, and on what basis."""

    unit: str
    obs: dict
    period: str
    conversion: str            # single_quarter | annual | point_in_time | ytd_delta | ytd_as_filed
    span_days: Optional[int] = None
    derived: bool = False
    formula: str = ""
    prior_end: str = ""


def _select_metric_observation(
    tagblock: dict, kind: str, metric: str, *, cash_flow_span: str = "quarter",
) -> Optional[Selection]:
    """Resolve the observation for one metric under the requested span policy."""
    if kind == "duration" and cash_flow_span == "ytd" and metric in CASH_FLOW_METRICS:
        return _select_ytd_observation(tagblock, metric)
    unit, obs = _select_observation(tagblock, kind)
    if obs is None:
        return None
    span = _span_days(obs.get("start"), obs.get("end"))
    return Selection(
        unit=unit, obs=obs, period=_period_label(obs),
        conversion=_conversion_label(kind, span), span_days=span,
    )


def _select_ytd_observation(tagblock: dict, metric: str) -> Optional[Selection]:
    """Newest cumulative cash-flow filing, reconciled to a single quarter.

    Cumulative rows of one fiscal year share the same period ``start``, so the
    prior observation is the one with the same fiscal year and start but an
    earlier end (9M -> 6M -> 3M). Differencing them yields the latest quarter.
    """
    candidates = _cumulative_candidates(tagblock)
    if not candidates:
        return None
    unit, latest_row = max(
        candidates,
        key=lambda c: (c[1].get("end", ""), c[1].get("filed", "")),
    )
    latest = dict(latest_row)
    latest["_tag"] = tagblock["_tag"]
    latest_span = _span_days(latest.get("start"), latest.get("end"))
    prior = _prior_cumulative(candidates, latest)
    if prior is None or latest.get("val") is None or prior.get("val") is None:
        return Selection(
            unit=unit, obs=latest, period=_period_label(latest),
            conversion="ytd_as_filed", span_days=latest_span,
        )

    delta_span = _span_days(prior.get("end"), latest.get("end"))
    reconciled = dict(latest)
    reconciled["val"] = latest["val"] - prior["val"]
    # Only a one-quarter difference is a quarter. Differencing a 9M row against a
    # 3M row yields a 6M span, which must be labeled as such rather than passed
    # off as a single quarter.
    if delta_span is not None and _is_quarter(delta_span):
        period, conversion = _quarter_label(latest, delta_span), "ytd_delta"
    else:
        period, conversion = _span_label(latest, delta_span), "ytd_delta_span"
    return Selection(
        unit=unit, obs=reconciled, period=period,
        conversion=conversion, span_days=delta_span, derived=True,
        formula=(f"{metric}({_period_label(latest)}) - {metric}({_period_label(prior)}) "
                 f"[cumulative companyfacts rows differenced to the {period} span]"),
        prior_end=prior.get("end", ""),
    )


def _cumulative_candidates(tagblock: dict) -> list[tuple[str, dict]]:
    """Rows that look like a fiscal-period flow: 3M / 6M / 9M / 12M spans."""
    out: list[tuple[str, dict]] = []
    for unit, rows in tagblock["units"].items():
        for row in rows:
            if "end" not in row or "val" not in row:
                continue
            span = _span_days(row.get("start"), row.get("end"))
            if span is not None and 80 <= span <= 380:
                out.append((unit, row))
    return out


def _prior_cumulative(candidates: list[tuple[str, dict]], latest: dict) -> Optional[dict]:
    """The cumulative row one quarter before ``latest``, when it exists.

    Preference order matters: a one-quarter difference is a quarter, while
    differencing against a much earlier row silently widens the span (9M - 3M is
    six months). Candidates are therefore ranked by "gives a quarter first", then
    by recency.
    """
    prior_pool = [
        row for _unit, row in candidates
        if row.get("fy") == latest.get("fy")
        and row.get("start") == latest.get("start")
        and row.get("end", "") < latest.get("end", "")
    ]
    if not prior_pool:
        return None
    prior_pool.sort(key=lambda row: (row.get("end", ""), row.get("filed", "")), reverse=True)
    for row in prior_pool:
        delta = _span_days(row.get("end"), latest.get("end"))
        if delta is not None and _is_quarter(delta):
            return row
    return prior_pool[0]


def _conversion_label(kind: str, span: Optional[int]) -> str:
    if kind != "duration":
        return "point_in_time"
    if span is not None and _is_quarter(span):
        return "single_quarter"
    if span is not None and _is_annual(span):
        return "annual"
    return "unknown"


def _first_present(taxonomy_block: dict, candidates: list[str]) -> Optional[dict]:
    """Pick the candidate tag with the most recent observation end.

    Issuers drift between tags over time (e.g. NVDA stopped filing
    PaymentsToAcquirePropertyPlantAndEquipment in 2011 and now uses
    PaymentsToAcquireProductiveAssets); candidate order alone would let the
    abandoned tag shadow the live one and surface decade-old values as latest.
    """
    best: Optional[dict] = None
    best_end = ""
    for tag in candidates:
        block = taxonomy_block.get(tag)
        if not (block and block.get("units")):
            continue
        last_end = max((row.get("end", "") for rows in block["units"].values()
                        for row in rows), default="")
        if last_end > best_end:
            best = dict(block)
            best["_tag"] = tag
            best_end = last_end
    return best


def _select_observation(tagblock: dict, kind: str) -> tuple[str, Optional[dict]]:
    """Pick the right observation for ``kind`` (period-aware).

    instant  -> latest by period end (balance-sheet point-in-time).
    duration -> latest clean quarter (~3-month span), else latest annual
                (~12-month). 6-/9-month YTD rows are skipped so a cumulative
                value is never mislabeled as a quarter.
    """
    candidates: list[tuple[str, dict]] = []
    for unit, rows in tagblock["units"].items():
        for row in rows:
            if "end" not in row or "val" not in row:
                continue
            if kind == "duration":
                span = _span_days(row.get("start"), row.get("end"))
                if span is None or not (_is_quarter(span) or _is_annual(span)):
                    continue
            candidates.append((unit, row))
    if not candidates:
        return "", None

    if kind == "duration":
        # prefer a clean quarter; within the chosen pool, latest end then filed.
        quarters = [c for c in candidates if _is_quarter(_span_days(c[1].get("start"), c[1].get("end")) or 0)]
        pool = quarters or candidates
        unit, row = max(pool, key=lambda c: (c[1].get("end", ""), c[1].get("filed", "")))
    else:
        unit, row = max(candidates, key=lambda c: (c[1].get("end", ""), c[1].get("filed", "")))

    obs = dict(row)
    obs["_tag"] = tagblock["_tag"]
    obs["_kind"] = kind
    return unit, obs


def _span_days(start: Optional[str], end: Optional[str]) -> Optional[int]:
    if not start or not end:
        return None
    try:
        d0 = _dt.date.fromisoformat(start)
        d1 = _dt.date.fromisoformat(end)
    except ValueError:
        return None
    return (d1 - d0).days


def _is_quarter(span: int) -> bool:
    return 80 <= span <= 100


def _is_annual(span: int) -> bool:
    return 350 <= span <= 380


def _period_label(obs: dict) -> str:
    """Label a claim by its real span — a cumulative value is never called a quarter."""
    fy, fp = obs.get("fy"), obs.get("fp")
    span = _span_days(obs.get("start"), obs.get("end"))
    if span is not None and _is_cumulative_ytd(span):
        return _span_label(obs, span)
    if fy and fp == "FY":
        return f"FY{fy}"
    if fy and fp:
        return f"FY{fy} {fp}"
    return obs.get("end", "unknown")


def _span_label(obs: dict, span: Optional[int]) -> str:
    """Label a value by its own span length, e.g. ``FY2026 9M``."""
    fy = obs.get("fy")
    if fy and span:
        return f"FY{fy} {_span_months(span)}M"
    return obs.get("end", "unknown")


def _quarter_label(obs: dict, delta_span: Optional[int]) -> str:
    """Label a differenced quarter: the issuer fiscal period when known."""
    fy, fp = obs.get("fy"), obs.get("fp")
    if fy and fp and fp != "FY":
        return f"FY{fy} {fp}"
    if fy and delta_span:
        return f"FY{fy} {_span_months(delta_span)}M"
    return obs.get("end", "unknown")


def _is_cumulative_ytd(span: int) -> bool:
    """A 6-/9-month year-to-date span: cumulative, neither a quarter nor a year."""
    return 150 <= span <= 330


def _span_months(span: Optional[int]) -> Optional[int]:
    if span is None:
        return None
    return max(1, round(span / 30.4375))
