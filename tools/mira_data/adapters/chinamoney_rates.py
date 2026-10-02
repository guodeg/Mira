"""China Money (中国货币网 / CFETS) rate adapters -> canonical ``macro_series``.

The genuinely **L2** macro source that a stdlib adapter can actually reach: CFETS is
the official interbank trading and benchmark-administration platform, and both rate
histories answer with plain ``application/json`` behind nothing but a browser UA.

Why not 国家统计局 (NBS), which would be the richer macro source
-------------------------------------------------------------
Probed live 2026-10-02: the retired ``easyquery.htm`` returns **403**, and the new
``/dg/website/publicrelease/web/external/new/*`` endpoints return **HTTP 200 with an
HTML page titled 服务异常** — an anti-bot block, not data. Getting through it needs TLS
fingerprinting (``curl_cffi``), which would break this repository's stdlib-only core, so
NBS is documented as out of reach rather than half-implemented. Rate benchmarks cover the
discount-rate side of A-share valuation, which is the part that actually feeds theses.

Contracts (probed live 2026-10-02)
----------------------------------
- ``GET https://www.chinamoney.com.cn/ags/ms/cm-u-bk-currency/LprHis`` with ``lang=CN``
  and optional ``startDate``/``endDate`` -> ``{head, data, records}`` where each record is
  ``{"1Y": "3.00", "5Y": "3.50", "showDateCN": "2026-09-20"}``. Loan Prime Rate is
  published **monthly (the 20th)**, so a one-year window is twelve records.
- ``GET https://www.chinamoney.com.cn/ags/ms/cm-u-bk-shibor/ShiborHis`` -> same envelope
  with tenors ``ON, 1W, 2W, 1M, 3M, 6M, 9M, 1Y`` and ``showDateCN``. Shibor is **daily**.

Two traps: the endpoints ignore an out-of-range ``startDate`` and silently return their
own default window (``data.startDateCN`` reports what was actually returned), and the
dates arrive as display strings (``showDateCN``) which are parsed explicitly rather than
trusting the response order.

Output shape: the latest observation per tenor becomes a claim, and the full returned
window rides along as a bulk side series when a single benchmark is requested (Mira's
``FetchResult.series`` exists for exactly this). Values are quoted in percent, not
converted to decimals, so the evidence log shows the published figure.
"""

from __future__ import annotations

import datetime as _dt
import re
import urllib.parse
from typing import Optional

from .. import config, net
from ..canonical import POSTURES, CanonicalRecord, FetchResult

LPR_URL = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-currency/LprHis"
SHIBOR_URL = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-shibor/ShiborHis"
LPR_ENDPOINT = "chinamoney://LprHis/{symbol}"
SHIBOR_ENDPOINT = "chinamoney://ShiborHis/{symbol}"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Referer": "https://www.chinamoney.com.cn/",
}

BENCHMARKS = ("LPR", "SHIBOR")
LPR_TENORS = ("1Y", "5Y")
SHIBOR_TENORS = ("ON", "1W", "2W", "1M", "3M", "6M", "9M", "1Y")
# Vendor tenor -> canonical metric
METRICS = {
    ("LPR", "1Y"): "lpr_1y",
    ("LPR", "5Y"): "lpr_5y",
    ("SHIBOR", "ON"): "shibor_on",
    ("SHIBOR", "1W"): "shibor_1w",
    ("SHIBOR", "2W"): "shibor_2w",
    ("SHIBOR", "1M"): "shibor_1m",
    ("SHIBOR", "3M"): "shibor_3m",
    ("SHIBOR", "6M"): "shibor_6m",
    ("SHIBOR", "9M"): "shibor_9m",
    ("SHIBOR", "1Y"): "shibor_1y",
}
_DATE_CN = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")


def _max_observations() -> int:
    """How many returned observations to keep per benchmark (newest first)."""
    raw = (config.get("MIRA_RATES_MAX_OBSERVATIONS") or "12").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 12


def _window_days() -> int:
    """Request window in days, clamped to the vendor's one-year history limit.

    Asking for more is not an error upstream: the endpoint answers HTTP 200 with an empty
    record list and ``data.message = 只提供一年历史数据查询及下载``, which would otherwise
    look like "no data" for the benchmark.
    """
    raw = (config.get("MIRA_RATES_WINDOW_DAYS") or "360").strip()
    try:
        days = int(raw)
    except ValueError:
        days = 360
    return max(30, min(365, days))


def fetch_macro_rates(
    benchmark: str = "BOTH",
    *,
    as_of: Optional[str] = None,
    market_scope: str = "CN",
) -> FetchResult:
    """Official Chinese benchmark rates: LPR, Shibor or both."""
    as_of = as_of or _dt.date.today().isoformat()
    wanted = benchmark.strip().upper()
    if wanted == "BOTH":
        names = list(BENCHMARKS)
    elif wanted in BENCHMARKS:
        names = [wanted]
    else:
        raise net.FetchError(f"invalid_benchmark: {benchmark!r} (use LPR, SHIBOR or BOTH)")

    records: list[CanonicalRecord] = []
    series: Optional[dict] = None
    errors: list[str] = []
    for name in names:
        try:
            history = _history(name, as_of=as_of)
        except net.FetchError as exc:
            errors.append(f"{name}: {exc}")
            continue
        if not history:
            errors.append(f"{name}: no observations returned")
            continue
        records.extend(_claims(name, history, as_of=as_of, market_scope=market_scope))
        if len(names) == 1:
            series = _series(name, history)
    if not records:
        raise net.FetchError("rates_source_gap: " + "; ".join(errors))
    records.sort(key=lambda r: (r.metric, r.period), reverse=True)
    return FetchResult(records, series=series)


def _history(benchmark: str, *, as_of: str) -> list[dict]:
    """Newest-first ``[{date, tenors...}]`` within the configured window."""
    url = LPR_URL if benchmark == "LPR" else SHIBOR_URL
    end = _dt.date.fromisoformat(as_of)
    start = end - _dt.timedelta(days=_window_days())
    query = urllib.parse.urlencode({
        "lang": "CN", "startDate": start.isoformat(), "endDate": end.isoformat(),
    })
    payload = net.get_json(f"{url}?{query}", headers=HEADERS, retries=2, backoff=1.5)
    rows = payload.get("records") or []
    data = payload.get("data") or {}
    window = {"vendorStart": data.get("startDateCN"), "vendorEnd": data.get("endDateCN")}
    if not rows:
        message = data.get("message") or data.get("messageEn") or "no observations returned"
        raise net.FetchError(f"{benchmark}: vendor returned no rows ({message})")
    history = []
    for row in rows:
        date = _parse_date(row.get("showDateCN"))
        if not date:
            continue
        history.append({"date": date, **{k: v for k, v in row.items() if k != "showDateCN"}})
    if not history:
        raise net.FetchError(f"{benchmark}: vendor rows carried no parsable date")
    history.sort(key=lambda item: item["date"], reverse=True)
    for item in history:
        item["_window"] = window
    return history[:_max_observations()]


def _claims(benchmark: str, history: list[dict], *, as_of: str,
            market_scope: str) -> list[CanonicalRecord]:
    posture = POSTURES["chinamoney_rates"]
    url = (LPR_ENDPOINT if benchmark == "LPR" else SHIBOR_ENDPOINT).format(symbol=benchmark)
    latest = history[0]
    window = latest.get("_window") or {}
    records = []
    tenors = LPR_TENORS if benchmark == "LPR" else SHIBOR_TENORS
    for tenor in tenors:
        value = _num(latest.get(tenor))
        if value is None:
            continue
        records.append(CanonicalRecord(
            family="macro_series", research_object=f"CN_{benchmark}", market_scope=market_scope,
            metric=METRICS[(benchmark, tenor)], value=value, unit="percent",
            period=latest["date"], period_type="point_in_time",
            as_of_date=as_of, source_date=latest["date"], posture=posture, url_or_path=url,
            provenance={
                "benchmark": benchmark, "tenor": tenor,
                "vendorField": tenor, "vendorUnit": "percent",
                "observedAt": latest["date"], "observations": len(history),
                "frequency": "monthly" if benchmark == "LPR" else "daily",
                "vendorWindow": f"{window.get('vendorStart')}..{window.get('vendorEnd')}",
            },
        ))
    return records


def _series(benchmark: str, history: list[dict]) -> dict:
    tenors = LPR_TENORS if benchmark == "LPR" else SHIBOR_TENORS
    columns = ["date", *[METRICS[(benchmark, t)] for t in tenors]]
    rows = [
        {"date": item["date"],
         **{METRICS[(benchmark, t)]: _num(item.get(t)) for t in tenors}}
        for item in sorted(history, key=lambda entry: entry["date"])
    ]
    return {"name": f"macro_rates-{benchmark}", "columns": columns, "rows": rows}


def _parse_date(text) -> str:
    match = _DATE_CN.search(str(text or ""))
    if not match:
        return ""
    year, month, day = (int(part) for part in match.groups())
    try:
        return _dt.date(year, month, day).isoformat()
    except ValueError:
        return ""


def _num(value):
    if value is None or isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None
