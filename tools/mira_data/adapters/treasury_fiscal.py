"""US Treasury Fiscal Data adapter -> canonical ``macro_series`` (L2, keyless).

The Treasury's own public API: no key, no quota, JSON, and the publishing agency is the
source — so these are L2 facts rather than the relays that carry the same ground at L5.

Two datasets are wired, both verified live 2026-10-03:

- ``debt`` — the daily Debt to the Penny: total public debt outstanding, split into debt held
  by the public and intragovernmental holdings (40,260,641,972,390.03 total that day).
- ``avg_interest`` — average interest rates on Treasury securities, one row per security type
  and description (bills, notes, bonds, TIPS, FRNs).

**Where this fits next to ``macro_fred``.** FRED already carries Treasury *yields* and credit
spreads, and reading them needs no new adapter — ``mira_data fetch macro_fred DGS10`` is the
route. What FRED does not carry is the Treasury's own debt-stock and average-interest
statistics, which is what this module adds. Keeping the two separate matters for tiering: a
yield from FRED is a market price, while total public debt is an official Treasury statistic.

Contract notes: the API paginates with ``page[size]``/``page[number]`` and reports
``meta.total-count``; fields arrive as **strings** including decimal amounts, so numeric
parsing is explicit; and ``record_date`` is the observation date while the response is
published later, so the two are recorded separately.
"""

from __future__ import annotations

import datetime as _dt
import urllib.parse
from typing import Optional

from .. import config, net
from ..canonical import POSTURES, CanonicalRecord, FetchResult

BASE = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
DEBT_URL = f"{BASE}/v2/accounting/od/debt_to_penny"
AVG_INTEREST_URL = f"{BASE}/v2/accounting/od/avg_interest_rates"
HEADERS = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")}
PAGE_SIZE = 100

DATASETS = {
    "debt": (DEBT_URL, "Debt to the Penny (total public debt outstanding)"),
    "avg_interest": (AVG_INTEREST_URL,
                     "Average interest rates on US Treasury securities"),
}


def fetch_treasury_series(
    dataset: str = "debt",
    *,
    as_of: Optional[str] = None,
    market_scope: str = "US",
    limit: Optional[int] = None,
) -> FetchResult:
    """Latest observation of one Treasury Fiscal Data dataset.

    ``dataset`` is ``debt`` or ``avg_interest``. For ``avg_interest`` the latest row of each
    security type is claimed separately, because the dataset mixes bills, notes, bonds, TIPS
    and FRNs and a single "latest" row would silently pick one of them.
    """
    as_of = as_of or _dt.date.today().isoformat()
    key = (dataset or "debt").strip().lower()
    if key not in DATASETS:
        raise net.FetchError(
            f"treasury_dataset_gap: {dataset!r} is not wired; this adapter reads "
            f"{'/'.join(sorted(DATASETS))}. The Treasury's exchange-rate dataset is also "
            "published here but a live probe of it failed at the network layer, so it is not "
            "claimed rather than guessed at")
    url_base, label = DATASETS[key]
    cap = max(1, min(1000, int(limit or _setting("MIRA_TREASURY_LIMIT", 200))))

    params = {"sort": "-record_date", "page[size]": str(cap), "page[number]": "1"}
    rows, total = _paged(url_base, params, cap)
    if not rows:
        raise net.FetchError(
            f"treasury_source_gap: no rows for the {key} dataset")
    posture = POSTURES["treasury_fiscal"]
    records, series_rows = [], []

    if key == "debt":
        latest = rows[0]
        value = _num(latest.get("tot_pub_debt_out_amt"))
        if value is None:
            raise net.FetchError(
                "treasury_source_gap: debt_to_penny returned no total public debt amount")
        day = str(latest.get("record_date") or "")
        for row in rows[:cap]:
            series_rows.append({k: row.get(k) for k in (
                "record_date", "debt_held_public_amt", "intragov_hold_amt",
                "tot_pub_debt_out_amt")})
        records.append(CanonicalRecord(
            family="macro_series", research_object="TREASURY_DEBT_TO_PENNY",
            market_scope=market_scope, metric="treasury_total_public_debt",
            value=value, unit="usd", period=day, period_type="point_in_time",
            as_of_date=as_of, source_date=day, posture=posture, url_or_path=url_base,
            claim_text=(f"美国未偿公共债务总额 {day} = ${value:,.0f}"
                        f"（公众持有 ${_num(latest.get('debt_held_public_amt')) or 0:,.0f}，"
                        f"政府内部持有 ${_num(latest.get('intragov_hold_amt')) or 0:,.0f}）"),
            provenance={
                "dataset": "debt_to_penny", "datasetId": "debt",
                "recordDate": day,
                "debtHeldByPublic": _num(latest.get("debt_held_public_amt")),
                "intragovernmentalHoldings": _num(latest.get("intragov_hold_amt")),
                "totalPublicDebtOutstanding": value,
                "fiscalYear": latest.get("record_fiscal_year"),
                "amountBasis": ("the API returns amounts as STRINGS including decimals; they "
                                "are parsed explicitly rather than coerced"),
                "publicationLagNote": ("record_date is the observation date; the response is "
                                       "published later, so both are recorded"),
                "rowsReturned": len(rows), "totalCount": total, "pageSize": cap,
            },
        ))
    else:
        # One claim per security type: the dataset mixes instruments, so "the latest row"
        # would silently choose whichever happens to sort first.
        by_type: dict[str, dict] = {}
        for row in rows:
            desc = str(row.get("security_desc") or "").strip()
            if desc and desc not in by_type:
                by_type[desc] = row
            series_rows.append({k: row.get(k) for k in (
                "record_date", "security_type_desc", "security_desc", "avg_interest_rate_amt")})
        if not by_type:
            raise net.FetchError(
                "treasury_source_gap: avg_interest_rates rows carried no security description")
        for desc, row in by_type.items():
            rate = _num(row.get("avg_interest_rate_amt"))
            if rate is None:
                continue
            day = str(row.get("record_date") or "")
            security_type = str(row.get("security_type_desc") or "").strip()
            records.append(CanonicalRecord(
                family="macro_series",
                research_object=f"TREASURY_AVG_INTEREST_{desc.upper().replace(' ', '_')}",
                market_scope=market_scope,
                metric=f"treasury_avg_interest_{_slug(desc)}", value=rate, unit="percent",
                period=day, period_type="point_in_time", as_of_date=as_of, source_date=day,
                posture=posture, url_or_path=url_base,
                claim_text=f"美国国债平均利率 {day} {security_type} {desc} = {rate}%",
                provenance={
                    "dataset": "avg_interest_rates", "datasetId": "avg_interest",
                    "recordDate": day, "securityType": security_type,
                    "securityDescription": desc, "averageInterestRatePercent": rate,
                    "perInstrumentNote": ("this dataset mixes bills, notes, bonds, TIPS and "
                                          "FRNs, so each security description is claimed "
                                          "separately rather than one 'latest' row"),
                    "rowsReturned": len(rows), "totalCount": total, "pageSize": cap,
                },
            ))
    if not records:
        raise net.FetchError(f"treasury_source_gap: no usable observation in {key}")
    series = {"name": f"treasury-{key}", "columns": list(series_rows[0].keys()),
              "rows": series_rows}
    return FetchResult(records, series=series)


def fetch_avg_interest(
    _symbol: str = "",
    *,
    as_of: Optional[str] = None,
    market_scope: str = "US",
    limit: Optional[int] = None,
) -> FetchResult:
    """Average interest rates, as a distinct family entry point.

    The CLI always passes the positional symbol, so a ``partial`` with ``dataset`` pre-bound
    collides ("multiple values for argument 'dataset'"). A named wrapper that swallows the
    ignored symbol is both simpler and honest about the family taking no symbol.
    """
    return fetch_treasury_series("avg_interest", as_of=as_of, market_scope=market_scope,
                                 limit=limit)


def _paged(url_base: str, params: dict, cap: int) -> tuple[list[dict], Optional[int]]:
    """Read up to ``cap`` rows, following the API's own pagination."""
    out: list[dict] = []
    page, total = 1, None
    while len(out) < cap:
        query = dict(params, **{"page[number]": str(page)})
        payload = net.get_json(url_base + "?" + urllib.parse.urlencode(query),
                               headers=HEADERS, retries=2, backoff=1.5)
        rows = payload.get("data") or []
        if total is None:
            total = (payload.get("meta") or {}).get("total-count")
        if not rows:
            break
        out.extend(rows)
        if len(rows) < int(query["page[size]"]):
            break
        page += 1
    return out[:cap], total


def _slug(text: str) -> str:
    import re
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
        return float(str(value).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None
