"""CSI (中证指数公司) official index data -> ``market_price`` / ``valuation_snapshot``.

The index compiler itself, so this is the authoritative source for its own index level,
composition, weights and valuation — the benchmark denominator the protocol names as
``benchmark_ohlcv`` (``data/public-source-targets.md:87``) and the fields
``templates/technical-analysis-check.csv`` expects (``benchmark``, ``relative_return_*``).
Keyless: a browser User-Agent plus Referer is the whole auth story.

Verified contracts (probed live 2026-10-02)
-------------------------------------------
- ``GET /csindex-home/indexInfo/index-basic-info/<code>`` -> profile JSON: Chinese and English
  names, ``basicDate``, ``basicIndex``, ``consNumber``, currency, type, description.
- ``GET /csindex-home/perf/index-perf?indexCode=<code>&startDate=YYYYMMDD&endDate=YYYYMMDD``
  -> daily rows: ``tradeDate open high low close change changePct tradingVol tradingValue
  consNumber``. **Dates must be ``YYYYMMDD``**: the dashed form returns HTTP 200 with an empty
  list, which reads like "no data" rather than like a bad request.
- ``GET /csindex-home/indexInfo/index-details-data?fileLang=1&indexCode=<code>`` -> pointers to
  the workbooks: 样本列表 ``<code>cons.xls``, 样本权重 ``<code>closeweight.xls``,
  指数估值 ``<code>indicator.xls`` (plus factsheet and methodology PDFs).

The workbooks are real OLE2/BIFF ``.xls`` (magic ``d0cf11e0a1b11ae1``), which the stdlib
cannot read, so ``xlrd`` is an **optional dependency** here in the same spirit as
``futu-api`` / ``ib_insync``: imported lazily, and a missing install degrades to
``csindex_dependency_gap`` instead of breaking the rest of the substrate.

Observed workbook shapes (downloaded and parsed, not assumed)
-------------------------------------------------------------
- ``cons.xls``: 301 rows x 9 cols, bilingual header row, one row per constituent:
  ``date, indexCode, indexName, indexNameEn, code, name, nameEn, exchange, exchangeEn``.
- ``closeweight.xls``: same plus a tenth column, ``weight(%)``. **Its date can differ from the
  constituent file's** (observed 20260831 weights against 20260930 composition), so both dates
  are recorded instead of one standing in for both.
- ``indicator.xls``: a short daily series of the index's own valuation:
  ``date, indexCode, nameCnFull, nameCn, nameEnFull, nameEn, pe_total, pe_calculated,
  dividend_yield_total, dividend_yield_calculated`` — **only the latest ~21 observations**
  (21 rows on 2026-10-02), so an index valuation percentile cannot be computed from a single
  read; that limit travels in provenance rather than being implied away.

Columns are read **positionally**: the header row is bilingual (``日期Date``, ``成份券代码
Constituent C``), so matching on header text would break on any wording change while the
column order is the stable part of the vendor's format.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional

from .. import config, net
from ..canonical import POSTURES, CanonicalRecord, FetchResult

HOME = "https://www.csindex.com.cn/csindex-home"
OSS = ("https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile")
PROFILE_PATH = "/indexInfo/index-basic-info/{code}"
PERF_PATH = "/perf/index-perf"
DETAILS_PATH = "/indexInfo/index-details-data"
ENDPOINT = "csindex://index-detail/{symbol}"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.csindex.com.cn/",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"),
}
MEMBER_FILES = {"constituents": "cons", "weights": "closeweight", "valuation": "indicator"}
DEFAULT_YEARS = 3
VALUATION_WINDOW_NOTE = ("the workbook carries only the latest ~21 observations, so an index "
                         "valuation percentile needs repeated reads over time")
COLUMNS = {
    "constituents": ("date", "index_code", "index_name", "index_name_en", "code", "name",
                     "name_en", "exchange", "exchange_en"),
    "weights": ("date", "index_code", "index_name", "index_name_en", "code", "name",
                "name_en", "exchange", "exchange_en", "weight"),
    "valuation": ("date", "index_code", "name_cn_full", "name_cn", "name_en_full", "name_en",
                  "pe_total", "pe_calculated", "dividend_yield_total",
                  "dividend_yield_calculated"),
}


def _years() -> int:
    try:
        return max(1, min(10, int((config.get("MIRA_CSINDEX_YEARS") or str(DEFAULT_YEARS)).strip())))
    except ValueError:
        return DEFAULT_YEARS


def _require_xlrd():
    try:
        import xlrd  # type: ignore
    except ImportError as exc:                       # pragma: no cover - depends on env
        raise net.FetchError(
            "csindex_dependency_gap: install the optional dependency 'xlrd' to read CSI index "
            "workbooks (the .xls files are OLE2/BIFF, which the stdlib cannot parse)") from exc
    return xlrd


def _parse_xls(raw: bytes) -> list[list]:
    """First sheet as a list of rows of raw cell values."""
    xlrd = _require_xlrd()
    book = xlrd.open_workbook(file_contents=raw)
    sheet = book.sheet_by_index(0)
    return [[sheet.cell_value(row, col) for col in range(sheet.ncols)]
            for row in range(sheet.nrows)]


# --------------------------------------------------------------------------- #
# benchmark: profile + official index history
# --------------------------------------------------------------------------- #

def fetch_index_benchmark(
    index_code: str,
    *,
    as_of: Optional[str] = None,
    market_scope: str = "CN",
) -> FetchResult:
    """Official index level history plus its profile — the benchmark denominator."""
    as_of = as_of or _dt.date.today().isoformat()
    code = _normalise_code(index_code)
    profile, profile_gap = _profile(code)
    rows = _perf(code, as_of=as_of)
    rows.sort(key=lambda row: str(row.get("tradeDate") or ""))
    latest = rows[-1]
    close = _number(latest.get("close"))
    if close is None:
        raise net.FetchError(f"csindex_source_gap: {code} has no close in the requested window")

    posture = POSTURES["csindex_index"]
    object_id = f"{code}.CSI"
    provenance = {
        "indexCode": code, "vendorIndexName": latest.get("indexNameCnAll"),
        "profileName": (profile or {}).get("indexFullNameCn"),
        "profileNameEn": (profile or {}).get("indexFullNameEn"),
        "baseDate": (profile or {}).get("basicDate"),
        "baseIndex": (profile or {}).get("basicIndex"),
        "profileStatus": profile_gap or "ok",
        "tradeDate": latest.get("tradeDate"),
        "changePct": _number(latest.get("changePct")),
        "tradingValue": _number(latest.get("tradingValue")),
        "tradingVolume": _number(latest.get("tradingVol")),
        "observations": len(rows),
        "quoteBasis": "official close published by the index compiler",
    }
    records = [CanonicalRecord(
        family="market_price", research_object=object_id, market_scope=market_scope,
        metric="index_close", value=close, unit="index_points",
        period=_iso_date(latest.get("tradeDate")), period_type="point_in_time",
        as_of_date=as_of, source_date=_iso_date(latest.get("tradeDate")),
        posture=posture, url_or_path=ENDPOINT.format(symbol=code),
        claim_text=f"{latest.get('indexNameCnAll') or code} 收盘 {close} "
                   f"({latest.get('tradeDate')})",
        provenance=dict(provenance),
    )]
    members = _number((profile or {}).get("consNumber")) or _number(latest.get("consNumber"))
    if members is not None:
        records.append(CanonicalRecord(
            family="market_price", research_object=object_id, market_scope=market_scope,
            metric="index_member_count", value=members, unit="count",
            period=_iso_date(latest.get("tradeDate")), period_type="point_in_time",
            as_of_date=as_of, source_date=_iso_date(latest.get("tradeDate")),
            posture=posture, url_or_path=ENDPOINT.format(symbol=code),
            claim_text=f"{latest.get('indexNameCnAll') or code} 成分股数量 {int(members)}",
            provenance=dict(provenance),
        ))
    series = {
        "name": f"index_benchmark-{code}",
        "columns": ["date", "open", "high", "low", "close", "change_pct", "trading_value"],
        "rows": [{"date": _iso_date(row.get("tradeDate")), "open": _number(row.get("open")),
                  "high": _number(row.get("high")), "low": _number(row.get("low")),
                  "close": _number(row.get("close")),
                  "change_pct": _number(row.get("changePct")),
                  "trading_value": _number(row.get("tradingValue"))}
                 for row in rows],
    }
    return FetchResult(records, series=series)


def _profile(code: str) -> tuple[Optional[dict], Optional[str]]:
    url = HOME + PROFILE_PATH.format(code=code)
    try:
        payload = net.get_json(url, headers=HEADERS, retries=2, backoff=1.5)
    except net.FetchError:
        return None, "profile_unavailable"
    data = payload.get("data")
    return (data if isinstance(data, dict) else None), (None if data else "profile_empty")


def _perf(code: str, *, as_of: str) -> list[dict]:
    day = _dt.date.fromisoformat(as_of)
    start = day.replace(year=day.year - _years())
    url = HOME + PERF_PATH + "?" + _query({
        "indexCode": code, "startDate": start.strftime("%Y%m%d"),
        "endDate": day.strftime("%Y%m%d")})
    payload = net.get_json(url, headers=HEADERS, retries=2, backoff=1.5)
    rows = payload.get("data") or []
    if not rows:
        raise net.FetchError(
            f"csindex_source_gap: {code} returned no index history for "
            f"{start.strftime('%Y%m%d')}-{day.strftime('%Y%m%d')} (dates must be YYYYMMDD; the "
            "dashed form answers 200 with an empty list)")
    return rows


# --------------------------------------------------------------------------- #
# members / weights, and index valuation — the .xls workbooks
# --------------------------------------------------------------------------- #

def fetch_index_members(
    index_code: str,
    *,
    as_of: Optional[str] = None,
    market_scope: str = "CN",
    with_weights: bool = True,
) -> FetchResult:
    """Index composition: one weight claim per constituent, plus the membership table."""
    as_of = as_of or _dt.date.today().isoformat()
    code = _normalise_code(index_code)
    posture = POSTURES["csindex_index"]
    rows, source = _workbook(code, "weights" if with_weights else "constituents")
    table = _table(rows, "weights" if with_weights else "constituents", code)
    if not table:
        raise net.FetchError(f"csindex_source_gap: {code} {source} workbook carried no rows")

    object_id = f"{code}.CSI"
    dates = sorted({row["date"] for row in table if row["date"]})
    provenance = {
        "indexCode": code, "vendorIndexName": table[0]["index_name"],
        "workbookKind": source, "workbookUrl": _workbook_url(code, source),
        "workbookDate": dates[-1] if dates else None,
        "workbookRows": len(table),
        "compositionCaveat": ("the constituents workbook and the weights workbook can carry "
                              "different dates (observed 20260930 composition against 20260831 "
                              "weights), so both are recorded when both are read"),
        "weightBasis": "weight(%) as published by the index compiler",
    }
    records: list[CanonicalRecord] = []
    if with_weights and all(row["weight"] is not None for row in table):
        for row in table:
            records.append(CanonicalRecord(
                family="market_price", research_object=object_id, market_scope=market_scope,
                metric="index_member_weight", value=row["weight"], unit="percent",
                period=_iso_date(row["date"]), period_type="point_in_time",
                as_of_date=as_of, source_date=_iso_date(row["date"]), posture=posture,
                url_or_path=ENDPOINT.format(symbol=code),
                claim_text=f"{row['code']} {row['name']} 在 {code} 权重 {row['weight']}% "
                           f"({row['date']})",
                provenance=dict(provenance, constituentCode=row["code"],
                                constituentName=row["name"], exchange=row["exchange"]),
            ))
    else:
        latest = dates[-1] if dates else ""
        records.append(CanonicalRecord(
            family="market_price", research_object=object_id, market_scope=market_scope,
            metric="index_member_count", value=len(table), unit="count",
            period=_iso_date(latest), period_type="point_in_time", as_of_date=as_of,
            source_date=_iso_date(latest), posture=posture,
            url_or_path=ENDPOINT.format(symbol=code),
            claim_text=f"{code} 成分股 {len(table)} 只（{latest}）",
            provenance=dict(provenance),
        ))
    series = {
        "name": f"index_members-{code}",
        "columns": (["date", "code", "name", "exchange", "weight"] if with_weights
                    else ["date", "code", "name", "exchange"]),
        "rows": [{"date": _iso_date(row["date"]), "code": row["code"], "name": row["name"],
                  "exchange": row["exchange"],
                  **({"weight": row["weight"]} if with_weights else {})} for row in table],
    }
    return FetchResult(records, series=series)


def fetch_index_valuation(
    index_code: str,
    *,
    as_of: Optional[str] = None,
    market_scope: str = "CN",
) -> FetchResult:
    """The compiler's own index valuation series (P/E and dividend yield, two bases)."""
    as_of = as_of or _dt.date.today().isoformat()
    code = _normalise_code(index_code)
    rows, source = _workbook(code, "valuation")
    table = _table(rows, "valuation", code)
    if not table:
        raise net.FetchError(f"csindex_source_gap: {code} valuation workbook carried no rows")
    table.sort(key=lambda row: row["date"])
    latest = table[-1]
    posture = POSTURES["csindex_index_valuation"]
    object_id = f"{code}.CSI"
    provenance = {
        "indexCode": code, "vendorIndexName": latest["name_cn_full"] or latest["name_cn"],
        "workbookKind": source, "workbookUrl": _workbook_url(code, source),
        "asOfDate": _iso_date(latest["date"]),
        "observations": len(table),
        "windowCaveat": VALUATION_WINDOW_NOTE,
        "ratioBasis": ("pe_total / dividend_yield_total 按总股本计算；pe_calculated / "
                       "dividend_yield_calculated 按计算用股本计算（沿用厂商列名）"),
    }
    specs = (("index_pe_total", "pe_total", "ratio"),
             ("index_pe_calculated", "pe_calculated", "ratio"),
             ("index_dividend_yield_total", "dividend_yield_total", "percent"),
             ("index_dividend_yield_calculated", "dividend_yield_calculated", "percent"))
    records = []
    for metric, field, unit in specs:
        value = latest.get(field)
        if value is None:
            continue
        records.append(CanonicalRecord(
            family="valuation_snapshot", research_object=object_id, market_scope=market_scope,
            metric=metric, value=value, unit=unit,
            period=_iso_date(latest["date"]), period_type="point_in_time",
            as_of_date=as_of, source_date=_iso_date(latest["date"]), posture=posture,
            url_or_path=ENDPOINT.format(symbol=code),
            claim_text=f"{code} {metric} = {value} ({latest['date']})",
            provenance=dict(provenance),
        ))
    series = {
        "name": f"index_valuation-{code}",
        "columns": ["date", "pe_total", "pe_calculated", "dividend_yield_total",
                    "dividend_yield_calculated"],
        "rows": [{"date": _iso_date(row["date"]), "pe_total": row["pe_total"],
                  "pe_calculated": row["pe_calculated"],
                  "dividend_yield_total": row["dividend_yield_total"],
                  "dividend_yield_calculated": row["dividend_yield_calculated"]}
                 for row in table],
    }
    return FetchResult(records, series=series)


def _workbook(code: str, kind: str) -> tuple[list[list], str]:
    """Download one workbook and return its rows plus the vendor's file name."""
    file_path = _workbook_url(code, kind)
    raw = net.get(file_path, headers=HEADERS, retries=2, backoff=1.5, timeout=90)
    if not raw:
        raise net.FetchError(f"csindex_source_gap: empty workbook from {file_path}")
    return _parse_xls(raw), kind


def _workbook_url(code: str, kind: str) -> str:
    details_url = HOME + DETAILS_PATH + "?" + _query({"fileLang": "1", "indexCode": code})
    payload = net.get_json(details_url, headers=HEADERS, retries=2, backoff=1.5)
    data = payload.get("data") or {}
    key = {"constituents": "样本列表", "weights": "样本权重", "valuation": "指数估值"}[kind]
    entries = data.get(key) or []
    for entry in entries:
        path = str(entry.get("filePath") or "")
        if path:
            return path
    raise net.FetchError(
        f"csindex_source_gap: the vendor listed no {key} workbook for {code}")


def _table(rows: list[list], kind: str, code: str) -> list[dict]:
    """Map workbook rows onto named fields positionally, skipping the bilingual header."""
    names = COLUMNS[kind]
    table = []
    for row in rows:
        cells = list(row) + [""] * (len(names) - len(row))
        first = _text(cells[0])
        if not first or first.startswith("日期") or first.lower().startswith("date"):
            continue
        record = {name: cells[index] for index, name in enumerate(names)}
        record["code"] = _member_code(record.get("code"))
        record["date"] = _text(record.get("date"))
        record["name"] = _text(record.get("name"))
        record["exchange"] = _text(record.get("exchange"))
        record["index_name"] = _text(record.get("index_name"))
        if "weight" in names:
            record["weight"] = _number(record.get("weight"))
        for field in ("pe_total", "pe_calculated", "dividend_yield_total",
                      "dividend_yield_calculated"):
            if field in names:
                record[field] = _number(record.get(field))
        if not record["code"] and not record.get("pe_total"):
            continue
        table.append(record)
    if kind in ("constituents", "weights"):
        wanted = [row for row in table if row.get("index_code") in ("", code)
                  or _text(row.get("index_code")).zfill(6) == code]
        table = wanted or table
    return table


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _query(params: dict) -> str:
    import urllib.parse
    return urllib.parse.urlencode(params)


def _normalise_code(value: str) -> str:
    text = str(value or "").strip().upper()
    if not text or len(text) > 10 or not text.replace(".", "").isalnum():
        raise net.FetchError(
            f"invalid_index_code: {value!r} (expected a CSI index code such as 000300 or H30374)")
    return text.split(".")[0]


def _text(value) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value or "").strip()


def _member_code(value) -> str:
    text = _text(value)
    return text.zfill(6) if text.isdigit() and len(text) <= 6 else text


def _number(value) -> Optional[float]:
    if value is None or isinstance(value, (int, float)):
        return float(value) if value is not None else None
    text = str(value).replace(",", "").replace("%", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _iso_date(value) -> str:
    text = _text(value)
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text

