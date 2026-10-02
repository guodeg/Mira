"""Eastmoney national-accounts relay adapter -> canonical ``macro_series``.

Fills the inflation/activity side of L2 that 国家统计局 cannot serve a stdlib adapter:
CPI, PPI, GDP and PMI. **This is a relay, not the official channel** — the content is
official statistics, but they are read from an aggregator's table, so the records are
stamped L5 ``reported_metric`` / ``reported_fact`` rather than L2 ``fact``, and every
record says so. The primary release is the NBS one, whose endpoints (probed 2026-10-02)
return 403 or an HTML page titled 服务异常; passing that needs TLS fingerprinting, which
this repository's stdlib-only core does not take. If an official adapter is ever added,
this one becomes its cross-check rather than its replacement.

Contracts (probed live 2026-10-02, all keyless)
----------------------------------------------
``GET https://datacenter-web.eastmoney.com/api/data/v1/get`` with ``reportName``,
``columns=ALL``, ``sortColumns=REPORT_DATE``, ``sortTypes=-1``, ``pageSize``, ``source=WEB``,
``client=WEB``. Verified report names and fields:

- ``RPT_ECONOMY_CPI`` (224 observations): ``NATIONAL_SAME`` year-over-year %, ``NATIONAL_SEQUENTIAL`` month-over-month %, ``NATIONAL_BASE`` index (same month last year = 100), ``TIME`` display period.
- ``RPT_ECONOMY_PPI`` (248): ``BASE_SAME`` YoY %, ``BASE`` index, ``BASE_ACCUMULATE`` cumulative index.
- ``RPT_ECONOMY_GDP`` (82): ``DOMESTICL_PRODUCT_BASE`` (亿元, note the vendor's spelling), ``SUM_SAME`` YoY %, per-industry fields.
- ``RPT_ECONOMY_PMI`` (225): ``MAKE_INDEX`` manufacturing, ``NMAKE_INDEX`` non-manufacturing, ``*_SAME`` changes.
- ``RPT_ECONOMY_MONEY_SUPPLY`` does **not** exist (the API answers 报表配置不存在), so money supply stays a documented gap.

Traps: the payload carries **no publish timestamp**, so the retrieval date is recorded as
the vintage and the vendor's ``TIME`` string is kept verbatim in provenance; and the GDP
row's ``REPORT_DATE`` is the quarter *end* while ``TIME`` reads ``2026年第1-2季度``, so the
period label is derived from the reported date and cross-checked against that string.
"""

from __future__ import annotations

import datetime as _dt
import re
import urllib.parse
from typing import Optional

from .. import config, net
from ..canonical import POSTURES, CanonicalRecord, FetchResult

API_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
ENDPOINT = "eastmoney://RPT_ECONOMY/{symbol}"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Referer": "https://data.eastmoney.com/",
}

# series -> (reportName, [(metric, vendor field, unit, scale to published unit)])
SERIES: dict[str, tuple[str, list[tuple[str, str, str]]]] = {
    "CPI": ("RPT_ECONOMY_CPI", [
        ("cpi_yoy", "NATIONAL_SAME", "percent"),
        ("cpi_mom", "NATIONAL_SEQUENTIAL", "percent"),
        ("cpi_index", "NATIONAL_BASE", "index"),
    ]),
    "PPI": ("RPT_ECONOMY_PPI", [
        ("ppi_yoy", "BASE_SAME", "percent"),
        ("ppi_index", "BASE", "index"),
    ]),
    "GDP": ("RPT_ECONOMY_GDP", [
        ("gdp_yoy", "SUM_SAME", "percent"),
        ("gdp_ytd", "DOMESTICL_PRODUCT_BASE", "CNY_100m"),
    ]),
    "PMI": ("RPT_ECONOMY_PMI", [
        ("pmi_manufacturing", "MAKE_INDEX", "index"),
        ("pmi_non_manufacturing", "NMAKE_INDEX", "index"),
    ]),
}
FREQUENCY = {"CPI": "monthly", "PPI": "monthly", "GDP": "quarterly", "PMI": "monthly"}
VINTAGE_CAVEAT = "vendor table carries no publish timestamp; vintage = retrieval date"
_MONTH = re.compile(r"(\d{4})年(\d{1,2})月份")
_QUARTER = re.compile(r"(\d{4})年第(\d)-(\d)季度")


def _max_observations() -> int:
    raw = (config.get("MIRA_MACRO_MAX_OBSERVATIONS") or "24").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 24


def fetch_macro_china(
    series: str = "ALL",
    *,
    as_of: Optional[str] = None,
    market_scope: str = "CN",
) -> FetchResult:
    """CPI / PPI / GDP / PMI from the aggregator relay, newest observation first."""
    as_of = as_of or _dt.date.today().isoformat()
    wanted = series.strip().upper()
    if wanted == "ALL":
        names = list(SERIES)
    elif wanted in SERIES:
        names = [wanted]
    else:
        raise net.FetchError(
            f"invalid_series: {series!r} (use {', '.join(SERIES)} or ALL)")

    records: list[CanonicalRecord] = []
    side_series: Optional[dict] = None
    errors: list[str] = []
    for name in names:
        try:
            history = _history(name)
        except net.FetchError as exc:
            errors.append(f"{name}: {exc}")
            continue
        records.extend(_claims(name, history, as_of=as_of, market_scope=market_scope))
        if len(names) == 1:
            side_series = _series(name, history)
    if not records:
        raise net.FetchError("macro_source_gap: " + "; ".join(errors))
    records.sort(key=lambda r: (r.metric, r.period), reverse=True)
    return FetchResult(records, series=side_series)


def _history(name: str) -> list[dict]:
    report_name, _fields = SERIES[name]
    params = {
        "reportName": report_name, "columns": "ALL", "pageNumber": "1",
        "pageSize": str(_max_observations()), "sortColumns": "REPORT_DATE",
        "sortTypes": "-1", "source": "WEB", "client": "WEB",
    }
    payload = net.get_json(API_URL + "?" + urllib.parse.urlencode(params),
                           headers=HEADERS, retries=2, backoff=1.5)
    result = payload.get("result") or {}
    rows = result.get("data") or []
    if not rows:
        message = payload.get("message") or "no rows returned"
        raise net.FetchError(f"vendor returned no rows ({message})")
    history = []
    for row in rows:
        period = _period_label(name, row)
        if not period:
            continue
        history.append({**row, "_period": period, "_reported": _iso_date(row.get("REPORT_DATE"))})
    if not history:
        raise net.FetchError("rows carried no usable period")
    history.sort(key=lambda item: item["_reported"] or item["_period"], reverse=True)
    return history[:_max_observations()]


def _claims(name: str, history: list[dict], *, as_of: str,
            market_scope: str) -> list[CanonicalRecord]:
    _report_name, fields = SERIES[name]
    posture = POSTURES["eastmoney_macro"]
    latest = history[0]
    records = []
    for metric, vendor_field, unit in fields:
        value = _num(latest.get(vendor_field))
        if value is None:
            continue
        records.append(CanonicalRecord(
            family="macro_series", research_object=f"CN_{name}", market_scope=market_scope,
            metric=metric, value=value, unit=unit,
            currency="CNY" if unit == "CNY_100m" else None,
            period=latest["_period"], period_type="calendar_period",
            as_of_date=as_of, source_date=as_of, posture=posture,
            url_or_path=ENDPOINT.format(symbol=name),
            provenance={
                "series": name, "vendorField": vendor_field, "vendorUnit": unit,
                "vendorPeriodLabel": latest.get("TIME"),
                "vendorReportTable": SERIES[name][0],
                "reportedDate": latest.get("_reported"),
                "frequency": FREQUENCY[name],
                "vintage": VINTAGE_CAVEAT,
                "authorityNote": "aggregator relay of an official release; the NBS primary "
                                 "is anti-bot blocked for a stdlib client",
            },
        ))
    return records


def _series(name: str, history: list[dict]) -> dict:
    _report_name, fields = SERIES[name]
    columns = ["period", *[metric for metric, _f, _u in fields]]
    rows = [
        {"period": item["_period"],
         **{metric: _num(item.get(vendor_field)) for metric, vendor_field, _u in fields}}
        for item in sorted(history, key=lambda entry: entry["_period"])
    ]
    return {"name": f"macro_china-{name}", "columns": columns, "rows": rows}


def _period_label(name: str, row: dict) -> str:
    """Monthly series -> ``YYYY-MM``; GDP -> ``YYYYQn`` (from the reported date)."""
    text = str(row.get("TIME") or "")
    if FREQUENCY[name] == "quarterly":
        match = _QUARTER.search(text)
        reported = _iso_date(row.get("REPORT_DATE"))
        if match and reported:
            year, _first, last = match.groups()
            return f"{year}Q{last}"
        if reported:
            year, month = reported[:4], int(reported[5:7])
            return f"{year}Q{(month - 1) // 3 + 1}"
        return ""
    match = _MONTH.search(text)
    if match:
        year, month = match.groups()
        return f"{year}-{int(month):02d}"
    reported = _iso_date(row.get("REPORT_DATE"))
    return reported[:7] if reported else ""


def _iso_date(value) -> str:
    text = str(value or "")
    return text[:10] if len(text) >= 10 else ""


def _num(value):
    if value is None or isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
