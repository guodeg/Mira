"""CFTC Commitments of Traders adapter -> canonical ``macro_series`` (L2, keyless).

The CFTC publishes the weekly COT report from its own Socrata endpoint: open interest plus
the long/short breakdown by trader category (non-commercial, commercial, total reportable,
non-reportable). This is the positioning series that sits behind "who is long this market",
and it is an official agency statistic, so it is L2 — unlike the same ground read from a
vendor, and different in kind from FRED's price and spread series.

Verified live 2026-10-03 against the financial-futures report (dataset ``6dca-aqww``): 133
fields per row, with ``noncomm_positions_long_all`` / ``noncomm_positions_short_all`` /
``comm_positions_long_all`` / ``comm_positions_short_all`` / ``open_interest_all`` and the
"old" versus "other" format columns for the legacy disaggregation.

Contract notes: rows are filtered by report date with Socrata's SoQL (``$where``), numbers
arrive as **strings**, and one row is one market for one report week — so each claim names both
the market and the report date, because "the latest COT" without a market is meaningless.
"""

from __future__ import annotations

import datetime as _dt
import re
import urllib.parse
from typing import Optional

from .. import config, net
from ..canonical import POSTURES, CanonicalRecord, FetchResult

# Socrata dataset ids. All four were probed live 2026-10-03 and each returned real rows; the
# initial guess that these were "financial" versus "combined" was WRONG — `6dca-aqww` serves
# WHEAT-SRW, i.e. it is the legacy futures-only report covering both commodity and financial
# markets. The labels below say what each dataset actually returned rather than what the name
# suggests, because a mislabelled report would misdescribe every position in it.
REPORTS = {
    "legacy": ("6dca-aqww", "Legacy futures-only report (commodity and financial markets)"),
    "disaggregated": ("72hh-3qpy", "Disaggregated futures-only report"),
    "tff": ("gpe5-46if", "Traders in Financial Futures (TFF) report"),
    "disaggregated_combined": ("jun7-fc8e", "Disaggregated combined futures-and-options report"),
}
DEFAULT_REPORT = "legacy"
SOCRATA_URL = "https://publicreporting.cftc.gov/resource/{dataset}.json"
HEADERS = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")}
DEFAULT_LIMIT = 200

# Canonical metric -> the report column that carries it.
METRICS = {
    "open_interest": "open_interest_all",
    "noncommercial_long": "noncomm_positions_long_all",
    "noncommercial_short": "noncomm_positions_short_all",
    "noncommercial_spread": "noncomm_postions_spread_all",     # CFTC's own spelling
    "commercial_long": "comm_positions_long_all",
    "commercial_short": "comm_positions_short_all",
    "nonreportable_long": "nonrept_positions_long_all",
    "nonreportable_short": "nonrept_positions_short_all",
}


def fetch_cot(
    market: Optional[str] = None,
    *,
    report: str = DEFAULT_REPORT,
    weeks: Optional[int] = None,
    as_of: Optional[str] = None,
    market_scope: str = "US",
    max_items: Optional[int] = None,
) -> FetchResult:
    """Positioning for one market (or the newest week of every market) as COT claims.

    ``market`` filters on the report's own market name, case-insensitively and as a substring
    (``"GOLD"``, ``"E-MINI S&P 500"``). Without it the newest report week is returned for every
    market, which is the useful sweep but also a wide one, so ``max_items`` bounds it.
    """
    as_of = as_of or _dt.date.today().isoformat()
    key = (report or DEFAULT_REPORT).strip().lower()
    if key not in REPORTS:
        raise net.FetchError(
            f"cftc_report_gap: {report!r} is not wired; this adapter reads "
            f"{'/'.join(sorted(REPORTS))}")
    dataset, label = REPORTS[key]
    limit = max(1, min(1000, int(max_items or _setting("MIRA_CFTC_MAX_ITEMS", DEFAULT_LIMIT))))

    params: dict[str, str] = {
        # Report date first, then OPEN INTEREST descending. The second key matters: a substring
        # filter like "GOLD" matches both "GOLD - COMMODITY EXCHANGE INC." (OI 406,456) and
        # "PAX GOLD PERP STYLE - COINBASE DERIVATIVES, LLC" (OI 1,512), and without this the
        # alphabetical order decided which one a caller saw. Now the economically significant
        # market comes first and the minor ones remain visible in the series below it.
        "$limit": str(limit),
        "$order": "report_date_as_yyyy_mm_dd DESC, open_interest_all DESC",
    }
    clauses = []
    if market:
        # SoQL string literals are single-quoted; the value is escaped rather than interpolated
        # raw, because an apostrophe in a market name would otherwise break the query.
        safe = str(market).replace("'", "''").upper()
        clauses.append(f"upper(market_and_exchange_names) like '%{safe}%'")
    if weeks:
        cutoff = (_dt.date.fromisoformat(as_of) - _dt.timedelta(weeks=int(weeks))).isoformat()
        clauses.append(f"report_date_as_yyyy_mm_dd >= '{cutoff}T00:00:00.000'")
    if clauses:
        params["$where"] = " AND ".join(clauses)

    url = SOCRATA_URL.format(dataset=dataset) + "?" + urllib.parse.urlencode(params)
    payload = net.get_json(url, headers=HEADERS, retries=2, backoff=1.5)
    rows = payload if isinstance(payload, list) else (payload.get("data") or [])
    if not rows:
        detail = f" matching {market!r}" if market else ""
        raise net.FetchError(
            f"cftc_source_gap: the {label} report returned no row{detail}; the COT report is "
            "weekly, so a very recent or very narrow market filter can legitimately be empty")

    posture = POSTURES["cftc_cot"]
    records, series_rows = [], []
    for row in rows:
        name = str(row.get("market_and_exchange_names") or "").strip()
        day = _day(row.get("report_date_as_yyyy_mm_dd"))
        if not name or not day:
            continue
        values = {metric: _num(row.get(col)) for metric, col in METRICS.items()}
        series_rows.append({"market": name, "report_date": day, **values})
        for metric, value in values.items():
            if value is None:
                continue
            records.append(CanonicalRecord(
                family="macro_series",
                research_object=f"COT_{_slug(name)[:60]}",
                market_scope=market_scope, metric=f"cot_{metric}", value=value,
                unit="contracts", period=day, period_type="point_in_time",
                as_of_date=as_of, source_date=day, posture=posture, url_or_path=url,
                claim_text=f"CFTC COT {day}「{name}」{metric} = {value:,.0f} 张",
                provenance={
                    "report": key, "dataset": dataset, "reportLabel": label,
                    "market": name, "reportDate": day,
                    "contractMarketCode": row.get("cftc_contract_market_code"),
                    "commodityName": row.get("commodity_name"),
                    "vendorColumn": METRICS[metric],
                    "units": "contracts (张)",
                    "weeklyNote": ("the COT report is weekly and reports positions as of "
                                   "Tuesday, published the following Friday, so the report date "
                                   "is not the publication date"),
                    "categoryNote": ("non-commercial is the speculative category, commercial "
                                     "the hedging one; 'nonreportable' are positions below the "
                                     "reporting threshold and are not a residual to be ignored"),
                    "rowsReturned": len(rows),
                },
            ))
    if not records:
        raise net.FetchError(
            f"cftc_source_gap: {label} returned {len(rows)} rows but none carried a usable "
            "market name, report date and position figure")
    series = {"name": "cftc-cot", "columns": list(series_rows[0].keys()), "rows": series_rows}
    return FetchResult(records, series=series)


def fetch_cot_family(
    market: Optional[str] = None,
    *,
    as_of: Optional[str] = None,
    market_scope: str = "US",
    weeks: Optional[int] = None,
    max_items: Optional[int] = None,
) -> FetchResult:
    """CLI entry point: the positional symbol IS the market filter.

    The CLI passes the symbol positionally, so the kwargs branch must not also pass
    ``market=`` — that raises "multiple values for argument 'market'". This wrapper makes the
    positional-to-keyword mapping explicit instead of relying on call-site discipline.
    """
    return fetch_cot(market, as_of=as_of, market_scope=market_scope, weeks=weeks,
                     max_items=max_items)


def _slug(text: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", text.lower())).strip("_") or "unknown"


def _setting(name: str, default: int) -> int:
    try:
        return int((config.get(name) or str(default)).strip())
    except ValueError:
        return default


def _num(value):
    if value is None or value == "":
        return None
    try:
        return float(re.sub(r"[, ]", "", str(value)))
    except ValueError:
        return None


def _day(value) -> str:
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    return match.group(0) if match else ""
