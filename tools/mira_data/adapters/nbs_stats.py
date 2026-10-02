"""National Bureau of Statistics (国家统计局) adapter -> canonical ``macro_series``.

**This is genuine L2 official data.** It replaces, for the series it covers, the
aggregator relay in ``eastmoney_macro``: the numbers come from the statistical agency's own
new (2026-03-27) data library, keyless, over a documented JSON API.

Correcting an earlier mistake in this repository
------------------------------------------------
A previous probe concluded that NBS needed TLS fingerprinting and was unreachable from a
stdlib client. That was wrong: the probe used **GET** against legacy paths, while the new
API is **POST + JSON** and answers plain ``urllib`` with a browser User-Agent and Referer.
Verified live 2026-10-02: 2.8 MB of JSON, ``success: true``, no key, no cookie, no
``curl_cffi``. The retired ``easyquery.htm`` really is gone (403), which is what the
2026-03-27 announcement "关于新版国家统计局数据发布库上线的公告" says.

API shape (base ``https://data.stats.gov.cn/dg/website/publicrelease/web/external``)
------------------------------------------------------------------------------------
1. ``GET /new/queryIndexTreeAsync?pid=&code=<1|2|3|...>`` -> catalog nodes ``{_id, name,
   isLeaf, sdate, edate}``. ``code``: 1 monthly, 2 quarterly, 3 annual, then provincial and
   city variants. ``pid`` empty returns the root, whose ``_id`` is the ``rootId``.
2. ``GET /new/queryIndicatorsByCid?cid=&dt=&rootId=<rootId>&name=<keyword>`` -> indicators
   ``{_id, catalogid, i_showname, i_mark, du, dp}``; ``i_mark`` is the statistical basis
   (口径) and ``i_showname`` embeds the base of an index, e.g. ``(上年同月=100)``.
3. ``POST /stream/esData`` with ``{cid, indicatorIds, daCatalogId, das, dts, showType,
   rootId}`` -> ``{data:[{code, name, values:[{_id, i_showname, value, du_name}]}]}``.
   ``showType`` is mandatory (omitting it is an HTTP 500); ``dts`` entries are
   ``YYYYMM+MM`` monthly, ``YYYY0Q+SS`` quarterly, ``YYYYYY``... annual ``YYYY+YY``, as
   ranges ``start-end``.

Traps handled here: unpublished periods come back as the literal string ``无`` rather than
being absent, so non-numeric values are dropped instead of becoming zeros; the indicator
identifiers are opaque UUIDs that are **not stable across time slices** (a long CPI history
can live in several ``cid`` values), so the adapter records the exact cid/indicator pair it
read and never pretends one call is the full history; and the response carries no publish
timestamp, so the retrieval date is the vintage while the period label comes from the
period code the vendor returned.

The statistical basis (``i_mark``) lives only in the catalog endpoint, not in the data
response, so it is fetched once per catalog and cached; a failure there degrades the
provenance note, never the data read.

Series deliberately **not** offered
----------------------------------
Total industrial profit (规模以上工业企业利润总额) is published by the agency, but the two
catalog slices that name it do not serve current data: probed live, one returns nothing at
all for 2011-2026 and the other stops at 2011-12, and the 135-indicator revenue catalog
carries profit only by industry. Since neither slice can answer "what is the current
print", the metric is left out instead of being approximated from a stale archive. Money
supply is absent for a different reason: it is central-bank data, not a statistical-agency
series.
"""

from __future__ import annotations

import datetime as _dt
import json
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .. import config, net
from ..canonical import POSTURES, CanonicalRecord, FetchResult

BASE = "https://data.stats.gov.cn/dg/website/publicrelease/web/external"
PAGE_URL = "https://data.stats.gov.cn/dg/website/page.html"
TREE_PATH = "/new/queryIndexTreeAsync"
INDICATORS_PATH = "/new/queryIndicatorsByCid"
DATA_PATH = "/stream/esData"
ENDPOINT = "nbs://stream/esData/{symbol}"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": PAGE_URL,
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"),
}
_SESSION: Optional[net.Session] = None


def _session() -> net.Session:
    """One warmed, cookie-keeping session per process.

    The library sits behind a Wangsu WAF: without its challenge cookie a request is
    answered with an intermittent 307 (observed live while fetching seven series in a
    row). The session warms up on the site page once, then reuses the cookie.
    """
    global _SESSION
    if _SESSION is None:
        _SESSION = net.Session(headers=HEADERS, warmup_url=PAGE_URL)
        _SESSION.warmup()
    return _SESSION


def _pause() -> float:
    try:
        return max(0.0, float((config.get("MIRA_NBS_PAUSE") or "0.6").strip()))
    except ValueError:
        return 0.6
# Roots verified live: pid= empty with each code returns that catalog's first node.
ROOTS = {
    "monthly": "fc982599aa684be7969d7b90b1bd0e84",
    "quarterly": "a94b8b7365a94874968cabbe392cf679",
    "annual": "884c062607104a91967b22742537f44f",
}
CATALOG_CODES = {"monthly": "1", "quarterly": "2", "annual": "3"}
UNPUBLISHED = {"", "无", "null", "None", "-"}
_REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Metric:
    name: str
    indicator_id: str
    unit: str
    currency: Optional[str] = None


@dataclass(frozen=True)
class Series:
    key: str
    label: str
    frequency: str
    cid: str
    metrics: list[Metric] = field(default_factory=list)


# Indicator ids resolved live on 2026-10-02 through the keyword search above; the vendor
# name (including the index base) is recorded in provenance on every record.
SERIES: dict[str, Series] = {
    "CPI": Series("CPI", "居民消费价格指数（全国）", "monthly",
                  "5c7452825c7c4dcba391db5ca7f335c5", [
                      Metric("cpi_index_yoy", "53180dfb9c14411ba4b762307c85920c",
                             "index_yoy_base100"),
                      Metric("cpi_index_mom", "f3904a1f5a384d54a3944ec6e2df3d1c",
                             "index_mom_base100"),
                  ]),
    "PPI": Series("PPI", "工业生产者出厂价格指数", "monthly",
                  "8bc27b5fd28e46df9b8fda8a5d336306", [
                      Metric("ppi_index_yoy", "06bb16735fc4416ca91c5f0efa476eef",
                             "index_yoy_base100"),
                      Metric("ppi_index_mom", "1e5ace697e6a44d4ba5e8a77704858de",
                             "index_mom_base100"),
                  ]),
    "PMI": Series("PMI", "制造业采购经理指数", "monthly",
                  "93ffbb1aa85740d3aa2618371508b606", [
                      Metric("pmi_manufacturing", "a09aa989bdcf4cffa2021795722eb916",
                             "percent"),
                  ]),
    # The other two legs of the published PMI trio live in their own catalog ids.
    "PMI_NON_MANUFACTURING": Series("PMI_NON_MANUFACTURING", "非制造业商务活动指数", "monthly",
                                    "7a64a6e25aec4a8e9dde044ecd9e2cce", [
                                        Metric("pmi_non_manufacturing",
                                               "88a150208f6e4a1db8babe41ae700f66", "percent"),
                                    ]),
    "PMI_COMPOSITE": Series("PMI_COMPOSITE", "综合PMI产出指数", "monthly",
                            "455378e1c3264a32875768a35ba5de76", [
                                Metric("pmi_composite",
                                       "55cdc89fa122446aa263912bdf14a540", "percent"),
                            ]),
    "UNEMPLOYMENT": Series("UNEMPLOYMENT", "城镇调查失业率", "monthly",
                           "ee3b7046b390415b9b7745e3d16f6052", [
                               Metric("unemployment_rate",
                                      "3888eac6062945a79c8a27e5f13d4953", "percent"),
                               Metric("unemployment_rate_31_cities",
                                      "1d550f3ec77a463bb607d4a3427e1465", "percent"),
                           ]),
    "RETAIL": Series("RETAIL", "社会消费品零售总额", "monthly",
                     "d0cb882c7f27443ab6b3ef9421901961", [
                         Metric("retail_sales_current", "1142a3a03e9045959e606a21822641ac",
                                "CNY_100m", "CNY"),
                         Metric("retail_sales_ytd", "260a1794443b43dd93a59928b12f38af",
                                "CNY_100m", "CNY"),
                         Metric("retail_sales_yoy", "aaac57d54d2e465d91bc9f3ea1a8618e",
                                "percent"),
                         Metric("retail_sales_ytd_yoy", "e3ca151b53d347b78d1e179e5ebf1d33",
                                "percent"),
                     ]),
    "FAI": Series("FAI", "固定资产投资（不含农户）", "monthly",
                  "5129067b149d4ddfbec1ffc478d35bfb", [
                      Metric("fai_ytd_yoy", "7e570cf8071c4734a7d78d9f0a70fbe1", "percent"),
                      Metric("fai_primary_ytd_yoy", "14f9561997d84321bd606c97977086bb",
                             "percent"),
                      Metric("fai_secondary_ytd_yoy", "92f72710d3ec46e79c6cb0e647a7fa91",
                             "percent"),
                      Metric("fai_tertiary_ytd_yoy", "eea023b7fb454ae6aebcf1aefe50a936",
                             "percent"),
                  ]),
    # Real estate is the single most A-share-relevant official block: property investment
    # and new-home sales feed both the developers and the whole downstream chain.
    "REALESTATE": Series("REALESTATE", "房地产开发投资", "monthly",
                         "9206137ccf03460daa74b7799e0f3c31", [
                             Metric("realestate_investment_ytd",
                                    "bfb626c0dfa04afab67937c452ca9f50", "CNY_100m", "CNY"),
                             Metric("realestate_investment_ytd_yoy",
                                    "205e08cba8c2409980db58c98da91b6f", "percent"),
                         ]),
    "PROPERTY_SALES": Series("PROPERTY_SALES", "新建商品房销售", "monthly",
                             "0ae633cdb85f4a8397650831b2b27e50", [
                                 Metric("property_sales_area_ytd",
                                        "d353226cf0434c929b6299f8d4987754", "10k_sqm"),
                                 Metric("property_sales_area_ytd_yoy",
                                        "50a37fbef1d04be68f15d82b711783bf", "percent"),
                             ]),
    # Industrial revenue survives the time-slice probe below; total industrial *profit*
    # does not (see the module docstring), so it is left out rather than approximated.
    "INDUSTRIAL_REVENUE": Series("INDUSTRIAL_REVENUE", "工业企业营业收入", "monthly",
                                 "95fa01deb3f64a7cbcecb9d888b16492", [
                                     Metric("industrial_revenue_ytd",
                                            "cfb5e8c8176b48c7bb5ae4911b212351",
                                            "CNY_100m", "CNY"),
                                     Metric("industrial_revenue_ytd_yoy",
                                            "491f3011a43847a0ab24f4e9a550b208", "percent"),
                                 ]),
    "INDUSTRIAL": Series("INDUSTRIAL", "规模以上工业增加值", "monthly",
                         "3f2e14f0542348ed9fe02476eca3450b", [
                             Metric("industrial_value_added_yoy",
                                    "ef1b1765960d45a29b4d7c4ca91be916", "percent"),
                             Metric("industrial_value_added_ytd_yoy",
                                    "21e7072e9f384209aedb56e69a18216e", "percent"),
                         ]),
    "INCOME": Series("INCOME", "居民人均可支配收入", "quarterly",
                     "ec2d57ed282f456e8d025aff035b4fad", [
                         Metric("household_income_per_capita_ytd",
                                "bb5699c5ad534b568cca7c946227225a", "CNY", "CNY"),
                         Metric("household_income_per_capita_ytd_yoy",
                                "7abcbb6f7c844b669d43da985d3f6ff2", "percent"),
                     ]),
    "GDP": Series("GDP", "国内生产总值", "quarterly",
                  "28d936104e304aa191e338eb82b6dc09", [
                      Metric("gdp_quarterly", "d22612f09aeb4241bc557ef0ac61b3ba",
                             "CNY_100m", "CNY"),
                      Metric("gdp_ytd", "8c5fab362d124fa7b91af833b3bd7397",
                             "CNY_100m", "CNY"),
                  ]),
}


def _months() -> int:
    try:
        return max(2, int((config.get("MIRA_NBS_MONTHS") or "36").strip()))
    except ValueError:
        return 36


def _cache_dir() -> Path:
    root = _REPO_ROOT / "local" / "mira-data-cache"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cache_days() -> int:
    try:
        return max(0, int((config.get("MIRA_NBS_CACHE_DAYS") or "1").strip()))
    except ValueError:
        return 1


def fetch_macro_nbs(
    series: str = "ALL",
    *,
    as_of: Optional[str] = None,
    market_scope: str = "CN",
) -> FetchResult:
    """Official NBS series: CPI, PPI, PMI, unemployment, retail sales, FAI, GDP."""
    as_of = as_of or _dt.date.today().isoformat()
    wanted = series.strip().upper()
    if wanted == "ALL":
        names = list(SERIES)
    else:
        # Accept "CPI,PPI" so one read can pull several series without a second process.
        names = [part.strip() for part in wanted.split(",") if part.strip()]
        unknown = [name for name in names if name not in SERIES]
        if unknown:
            raise net.FetchError(
                f"invalid_series: {', '.join(unknown)} "
                f"(use {', '.join(SERIES)} or ALL, comma-separated)")
        names = list(dict.fromkeys(names))

    records: list[CanonicalRecord] = []
    side_series: Optional[dict] = None
    errors: list[str] = []
    for index, name in enumerate(names):
        entry = SERIES[name]
        if index:
            time.sleep(_pause())        # the WAF throttles back-to-back reads
        try:
            periods, basis = _read(entry, as_of=as_of)
        except net.FetchError as exc:
            errors.append(f"{name}: {exc}")
            continue
        records.extend(_claims(entry, periods, basis, as_of=as_of, market_scope=market_scope))
        if len(names) == 1:
            side_series = _series(entry, periods)
    if not records:
        raise net.FetchError("nbs_source_gap: " + "; ".join(errors))
    records.sort(key=lambda r: (r.metric, r.period), reverse=True)
    return FetchResult(records, series=side_series)


def _read(entry: Series, *, as_of: str) -> tuple[list[dict], dict]:
    root = ROOTS[entry.frequency]
    payload = {
        "cid": entry.cid,
        "indicatorIds": [metric.indicator_id for metric in entry.metrics],
        "daCatalogId": "",
        "das": [{"text": "全国", "value": "000000000000"}],
        "dts": [_dts_window(entry.frequency, as_of)],
        "showType": "1",
        "rootId": root,
    }
    body = _session().post_json(BASE + DATA_PATH, payload, retries=2, backoff=1.5)
    if not body.get("success"):
        raise net.FetchError(f"vendor reported {body.get('message') or 'failure'}")
    periods = []
    for period in body.get("data") or []:
        code = str(period.get("code") or "")
        values = {}
        for value in period.get("values") or []:
            indicator = str(value.get("_id") or "")
            raw = value.get("value")
            if raw is None or str(raw).strip() in UNPUBLISHED:
                continue        # unpublished periods come back as 无, not as an absent key
            try:
                values[indicator] = float(str(raw).replace(",", "").strip())
            except ValueError:
                continue
        if values:
            periods.append({"code": code, "label": _period_label(code, entry.frequency),
                            "values": values})
    if not periods:
        raise net.FetchError(f"no published observation in {_dts_window(entry.frequency, as_of)}")
    periods.sort(key=lambda item: item["label"], reverse=True)
    return periods, _basis(entry)


def _claims(entry: Series, periods: list[dict], basis: dict, *, as_of: str,
            market_scope: str) -> list[CanonicalRecord]:
    posture = POSTURES["nbs_stats"]
    latest = periods[0]
    notes = basis.get("notes") or {}
    records = []
    for metric in entry.metrics:
        value = latest["values"].get(metric.indicator_id)
        if value is None:
            continue
        records.append(CanonicalRecord(
            family="macro_series", research_object=f"CN_{entry.key}",
            market_scope=market_scope, metric=metric.name, value=value, unit=metric.unit,
            currency=metric.currency,
            period=latest["label"], period_type="calendar_period",
            as_of_date=as_of, source_date=as_of, posture=posture,
            url_or_path=ENDPOINT.format(symbol=entry.key),
            provenance={
                "series": entry.key, "vendorSeries": entry.label,
                "vendorMetric": notes.get(metric.indicator_id, {}).get("showname"),
                "statisticalBasis": notes.get(metric.indicator_id, {}).get("mark"),
                "basisStatus": basis.get("error") or "ok",
                "vendorPeriodCode": latest["code"],
                "frequency": entry.frequency,
                "catalogId": entry.cid, "indicatorId": metric.indicator_id,
                "rootId": ROOTS[entry.frequency],
                "region": "全国", "regionCode": "000000000000",
                "vintage": "release carries no publish timestamp; vintage = retrieval date",
                "timeSlicing": "the vendor splits one series across catalog ids by period; "
                               "this record covers only the catalog id above",
            },
        ))
    return records


def _series(entry: Series, periods: list[dict]) -> dict:
    columns = ["period", *[metric.name for metric in entry.metrics]]
    rows = [
        {"period": item["label"],
         **{metric.name: item["values"].get(metric.indicator_id) for metric in entry.metrics}}
        for item in sorted(periods, key=lambda element: element["label"])
    ]
    return {"name": f"macro_nbs-{entry.key}", "columns": columns, "rows": rows}


def _dts_window(frequency: str, as_of: str) -> str:
    day = _dt.date.fromisoformat(as_of)
    if frequency == "quarterly":
        years = max(2, _months() // 12)
        start = f"{day.year - years}01SS"
        quarter = (day.month - 1) // 3 + 1
        return f"{start}-{day.year}0{quarter}SS"
    months = _months()
    total = day.year * 12 + (day.month - 1) - (months - 1)
    start_year, start_month = divmod(total, 12)
    return f"{start_year}{start_month + 1:02d}MM-{day.year}{day.month:02d}MM"


def _period_label(code: str, frequency: str) -> str:
    if frequency == "quarterly" and len(code) >= 8:
        return f"{code[:4]}Q{int(code[4:6])}"
    if len(code) >= 6:
        return f"{code[:4]}-{code[4:6]}"
    return code


def _basis(entry: Series) -> dict:
    """Fetch ``i_mark`` (口径) for the series' indicators, cached; never fatal."""
    path = _cache_dir() / f"nbs-catalog-{entry.cid}.json"
    data = None
    if path.exists() and _cache_days() > 0:
        age = (_dt.datetime.now() - _dt.datetime.fromtimestamp(path.stat().st_mtime)).days
        if age < _cache_days():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = None
    if data is None:
        url = (BASE + INDICATORS_PATH + "?" + urllib.parse.urlencode(
            {"cid": entry.cid, "dt": "", "name": ""}))
        try:
            payload = _session().get_json(url, retries=2, backoff=1.5)
            data = ((payload.get("data") or {}).get("list")) or []
            try:
                path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
        except net.FetchError:
            return {"notes": {}, "error": "catalog_unavailable"}
    notes = {}
    for item in data or []:
        notes[str(item.get("_id"))] = {
            "showname": (item.get("i_showname") or "").strip(),
            "mark": (item.get("i_mark") or "").strip() or None,
            "unit": item.get("du_name") or item.get("du"),
        }
    return {"notes": notes}


def search_indicators(keyword: str, *, frequency: str = "monthly") -> list[dict]:
    """Keyword -> indicator ids for discovery (``nbs search``)."""
    if frequency not in ROOTS:
        raise net.FetchError(f"invalid_frequency: {frequency!r} (use monthly/quarterly/annual)")
    url = (BASE + INDICATORS_PATH + "?" + urllib.parse.urlencode(
        {"cid": "", "dt": "", "rootId": ROOTS[frequency], "name": keyword}))
    payload = _session().get_json(url, retries=2, backoff=1.5)
    rows = ((payload.get("data") or {}).get("list")) or []
    return [
        {"indicatorId": str(row.get("_id")), "catalogId": row.get("catalogid"),
         "name": (row.get("i_showname") or "").strip(), "mark": row.get("i_mark")}
        for row in rows
    ]
