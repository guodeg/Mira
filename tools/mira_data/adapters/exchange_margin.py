"""SSE / SZSE margin-financing (融资融券) adapters -> canonical records.

This is genuinely **L2**: both endpoints are hosted by the exchanges themselves
(``query.sse.com.cn`` / ``www.szse.cn``), unlike the 龙虎榜/大宗/北向 paths that
every open-source library reaches through a commercial aggregator. Margin balance is
the mainland analogue of short interest and leverage positioning, so per-name reads
land in ``ownership_short_interest`` and the market aggregates in ``macro_series``.

Verified contract (probed live 2026-10-02)
------------------------------------------
- SSE summary + per-name detail: ``GET https://query.sse.com.cn/marketdata/tradedata/queryMargin.do``
  with ``Referer: https://www.sse.com.cn/``. The response is a list of **named**
  dicts (``rzye`` 融资余额, ``rzmre`` 融资买入额, ``rzche`` 融资偿还额, ``rqyl`` 融券余量,
  ``rqmcl`` 融券卖出量, ``rqchl`` 融券偿还量, ``rqylje`` 融券余量金额, ``rzrqjyzl`` 融资融券余额).
  ``tabType=mxtype`` + ``detailsDate`` + ``stockCode`` filters to one name **server-side**,
  so a single-name read costs one request.
- SZSE: ``GET https://www.szse.cn/api/report/ShowReport/data`` with
  ``SHOWTYPE=JSON&CATALOGID=1837_xxpl&txtDate=YYYY-MM-DD``, ``Referer`` the SZSE margin
  page. Response is an array of two tables: index 0 is 融资融券交易总量 (market) and
  index 1 is 融资融券交易明细 (per stock). SZSE abbreviates: ``jrrzmr`` 融资买入额,
  ``jrrzye`` 融资余额, ``jrrjmc`` 融券卖出量, ``jrrjyl`` 融券余量, ``jrrjye`` 融券余额,
  ``jrrzrjye`` 融资融券余额, ``zqdm`` 证券代码, ``zqjc`` 证券简称.

Traps this adapter absorbs
--------------------------
1. **The two exchanges use different units.** SSE reports 元 and 股; SZSE reports
   亿元 and 万股 (cross-checked: SSE 融资余额 12,965.21 亿元 vs SZSE 12,657.14 亿元 for
   the same week — same magnitude only after ×1e8). Values are normalised to 元/股 and
   the raw vendor value plus vendor unit stay in provenance so the conversion is auditable.
2. **Recent dates can be missing.** SZSE had 2026-09-18 but returned zero rows for
   2026-09-25 and 2026-09-30 while SSE already had 2026-09-30. A single-date read would
   therefore fail for reasons that have nothing to do with the name, so the adapter walks
   back up to ``MIRA_MARGIN_MAX_BACKFILL_DAYS`` days and records both the requested and the
   used date rather than silently shifting the period.
3. **SZSE has no server-side per-name filter** — the daily detail is the whole market
   (2105 rows / 106 pages on 2026-09-18). Bulk-pulling it is exactly what DATA_POLICY
   forbids, but the rows are sorted by 证券代码 ascending, so the adapter binary-searches
   the page range (~7 requests) and reports a source gap when the code is not in the
   margin-eligible universe.
4. **北交所 is not covered here**: its margin data is published by the BSE, not by SSE or
   SZSE, so ``.BJ`` names degrade to a labelled source gap instead of a wrong answer.
"""

from __future__ import annotations

import datetime as _dt
import urllib.parse
from typing import Optional

from .. import config, net
from ..canonical import POSTURES, CanonicalRecord, FetchResult
from .hithink_finance import resolve_thscode  # shared A-share symbol -> thscode map

SSE_URL = "https://query.sse.com.cn/marketdata/tradedata/queryMargin.do"
SZSE_URL = "https://www.szse.cn/api/report/ShowReport/data"
SSE_ENDPOINT = "sse-margin://queryMargin.do/{symbol}"
SZSE_ENDPOINT = "szse-margin://ShowReport/1837_xxpl/{symbol}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
SSE_HEADERS = {"User-Agent": UA, "Referer": "https://www.sse.com.cn/"}
SZSE_HEADERS = {"User-Agent": UA, "Referer": "https://www.szse.cn/disclosure/margin/margin/index.html"}

# vendor field -> (metric, unit, multiplier to normalise into 元 / 股)
SSE_FIELDS = {
    "rzye": ("margin_financing_balance", "CNY", 1.0),
    "rzmre": ("margin_financing_buy", "CNY", 1.0),
    "rzche": ("margin_financing_repay", "CNY", 1.0),
    "rqyl": ("margin_short_balance_volume", "shares", 1.0),
    "rqmcl": ("margin_short_sell_volume", "shares", 1.0),
    "rqchl": ("margin_short_repay_volume", "shares", 1.0),
    "rqylje": ("margin_short_balance_value", "CNY", 1.0),
    "rzrqjyzl": ("margin_total_balance", "CNY", 1.0),
}
SZSE_FIELDS = {
    "jrrzye": ("margin_financing_balance", "CNY", 1e8),      # 亿元 -> 元
    "jrrzmr": ("margin_financing_buy", "CNY", 1e8),
    "jrrjyl": ("margin_short_balance_volume", "shares", 1e4),  # 万股 -> 股
    "jrrjmc": ("margin_short_sell_volume", "shares", 1e4),
    # The factor here is a placeholder: 融券余额 is resolved by _szse_rjye below,
    # which infers 万元 vs 亿元 per table from the vendor's own identity.
    "jrrjye": ("margin_short_balance_value", "CNY", 1e8),
    "jrrzrjye": ("margin_total_balance", "CNY", 1e8),
}
# 融券余额 (jrrjye) is the one field the vendor expresses differently per table:
# 万元 in the detail table but 亿元 in the market table. Verified against the vendor's
# own identity 融资余额 + 融券余额 = 融资融券余额 — for 平安银行 2026-09-18,
# 45.80亿 + 11,559.99万元 = 46.96亿 only closes when the detail value is 万元, while the
# market row 12,657.14 + 108.15 = 12,765.29 only closes when it is 亿元. Getting this
# wrong is a silent 10,000x error, so the factor is inferred from that identity rather
# than hardcoded, and the basis is recorded per record.
SZSE_RJYE_FIELD = "jrrjye"
SZSE_RJYE_TABLE_DEFAULT = {1: 1e8, 2: 1e4}       # market table: 亿元, detail table: 万元
EXCHANGES = ("SSE", "SZSE")


def _max_backfill_days() -> int:
    """How far back to walk when the requested day has no publication yet.

    Fourteen days, not a couple: SZSE lagged SSE by four trading days on 2026-10-02
    (SSE had 09-30, SZSE's latest was 09-24), and a long holiday such as 国庆 can widen
    that further. Walking back is always reported through the requested/used dates.
    """
    raw = (config.get("MIRA_MARGIN_MAX_BACKFILL_DAYS") or "14").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 14


def _max_search_pages() -> int:
    raw = (config.get("MIRA_MARGIN_MAX_SEARCH_PAGES") or "12").strip()
    try:
        return max(2, int(raw))
    except ValueError:
        return 12


# --------------------------------------------------------------------------- #
# fetchers
# --------------------------------------------------------------------------- #

def fetch_margin_market(
    exchange: str = "BOTH",
    *,
    date: Optional[str] = None,
    as_of: Optional[str] = None,
    market_scope: str = "CN",
) -> FetchResult:
    """Market-wide margin aggregates for SSE, SZSE or both (family ``macro_series``)."""
    as_of = as_of or _dt.date.today().isoformat()
    wanted = [exchange.strip().upper()] if exchange.strip().upper() in EXCHANGES else list(EXCHANGES)
    if exchange.strip().upper() not in EXCHANGES and exchange.strip().upper() != "BOTH":
        raise net.FetchError(f"invalid_exchange: {exchange!r} (use SSE, SZSE or BOTH)")

    records: list[CanonicalRecord] = []
    gaps: list[str] = []
    for name in wanted:
        try:
            records.extend(_market_records(name, date=date, as_of=as_of, market_scope=market_scope))
        except net.FetchError as exc:
            gaps.append(f"{name}: {exc}")
    if not records:
        raise net.FetchError("margin_source_gap: " + "; ".join(gaps))
    return FetchResult(records)


def fetch_margin_balance(
    symbol: str,
    *,
    date: Optional[str] = None,
    as_of: Optional[str] = None,
    market_scope: str = "CN",
) -> FetchResult:
    """Per-name margin financing and securities lending (family ``ownership_short_interest``)."""
    as_of = as_of or _dt.date.today().isoformat()
    thscode = resolve_thscode(symbol)
    code, board = thscode.split(".")
    if board == "BJ":
        raise net.FetchError(
            "margin_source_gap: Beijing Stock Exchange margin data is published by the BSE, "
            f"not by SSE/SZSE ({thscode}); no adapter covers it yet")
    if board == "SH":
        return _sse_name(thscode, code, date=date, as_of=as_of, market_scope=market_scope)
    return _szse_name(thscode, code, date=date, as_of=as_of, market_scope=market_scope)


# --------------------------------------------------------------------------- #
# SSE
# --------------------------------------------------------------------------- #

def _sse_rows(*, tab_type: str = "", begin: str = "", end: str = "",
              details_date: str = "", stock_code: str = "") -> list[dict]:
    params = {
        "isPagination": "true", "tabType": tab_type, "beginDate": begin, "endDate": end,
        "detailsDate": details_date, "stockCode": stock_code,
        "pageHelp.pageSize": "5000", "pageHelp.pageNo": "1", "pageHelp.beginPage": "1",
        "pageHelp.cacheSize": "1", "pageHelp.endPage": "5",
    }
    payload = net.get_json(SSE_URL + "?" + urllib.parse.urlencode(params), headers=SSE_HEADERS)
    rows = payload.get("result") if isinstance(payload, dict) else None
    return [row for row in (rows or []) if isinstance(row, dict)]


def _market_records(exchange: str, *, date: Optional[str], as_of: str,
                    market_scope: str) -> list[CanonicalRecord]:
    if exchange == "SSE":
        rows = _sse_rows(begin=_compact(date), end=_compact(date)) if date else _sse_rows()
        row, used = _pick_latest(rows, "opDate", date)
        posture, url = POSTURES["sse_margin"], SSE_ENDPOINT.format(symbol="MARKET")
        fields, provenance_extra, rjye_table = SSE_FIELDS, {"vendorUnit": "CNY/shares"}, None
    else:
        row, used = _szse_market_row(date)
        posture, url = POSTURES["szse_margin"], SZSE_ENDPOINT.format(symbol="MARKET")
        fields = SZSE_FIELDS
        provenance_extra, rjye_table = {"vendorUnit": "CNY_100m/10000_shares"}, 1
    if row is None:
        raise net.FetchError("no usable market rows")
    records = _build(row, fields=fields, posture=posture, url=url,
                     research_object=f"{exchange}_MARGIN", family="macro_series",
                     date=used, as_of=as_of, market_scope=market_scope,
                     provenance_extra=provenance_extra, requested_date=date,
                     rjye_table=rjye_table)
    if not records:
        raise net.FetchError("market row carried no usable margin fields")
    return records


def _sse_name(thscode: str, code: str, *, date: Optional[str], as_of: str,
              market_scope: str) -> FetchResult:
    for candidate in _candidate_dates(date):
        rows = _sse_rows(tab_type="mxtype", details_date=_compact(candidate), stock_code=code)
        if rows:
            records = _build(rows[0], fields=SSE_FIELDS, posture=POSTURES["sse_margin"],
                             url=SSE_ENDPOINT.format(symbol=thscode),
                             research_object=thscode, family="ownership_short_interest",
                             date=_iso(rows[0].get("opDate")) or candidate, as_of=as_of,
                             market_scope=market_scope,
                             provenance_extra={"vendorUnit": "CNY/shares", "securityAbbr": rows[0].get("securityAbbr")},
                             requested_date=date)
            if records:
                return FetchResult(records)
    raise net.FetchError(
        f"margin_source_gap: SSE returned no margin detail for {thscode} within "
        f"{_max_backfill_days()} days of {date or 'today'} (not margin-eligible, or pass "
        f"--date with a day the exchange has published)")


# --------------------------------------------------------------------------- #
# SZSE
# --------------------------------------------------------------------------- #

def _szse_table(date: str, page: int = 1, table: int = 1) -> dict:
    params = {
        "SHOWTYPE": "JSON", "CATALOGID": "1837_xxpl", "txtDate": date,
        f"tab{table}PAGENO": str(page), "random": "0.7425245522795993",
    }
    payload = net.get_json(SZSE_URL + "?" + urllib.parse.urlencode(params), headers=SZSE_HEADERS)
    entries = payload if isinstance(payload, list) else []
    if len(entries) <= table - 1 or not isinstance(entries[table - 1], dict):
        raise net.FetchError("szse_bad_output: expected two report tables")
    return entries[table - 1]


def _szse_market_row(date: Optional[str]) -> tuple[Optional[dict], str]:
    for candidate in _candidate_dates(date):
        table = _szse_table(candidate, page=1, table=1)
        rows = table.get("data") or []
        if rows:
            return rows[0], candidate
    return None, date or ""


def _szse_name(thscode: str, code: str, *, date: Optional[str], as_of: str,
               market_scope: str) -> FetchResult:
    for candidate in _candidate_dates(date):
        table = _szse_table(candidate, page=1, table=2)
        rows = table.get("data") or []
        if not rows:
            continue
        found = _szse_find_code(code, candidate, table)
        if found is None:
            continue
        records = _build(found, fields=SZSE_FIELDS, posture=POSTURES["szse_margin"],
                         url=SZSE_ENDPOINT.format(symbol=thscode),
                         research_object=thscode, family="ownership_short_interest",
                         date=candidate, as_of=as_of, market_scope=market_scope,
                         provenance_extra={"vendorUnit": "CNY_100m/10000_shares",
                                           "securityAbbr": found.get("zqjc")},
                         requested_date=date, rjye_table=2)
        if records:
            return FetchResult(records)
    raise net.FetchError(
        f"margin_source_gap: SZSE returned no margin detail for {thscode} within "
        f"{_max_backfill_days()} days of {date or 'today'} (not margin-eligible, or pass "
        f"--date with a day the exchange has published)")


def _szse_find_code(code: str, date: str, table: dict) -> Optional[dict]:
    """Binary-search the code-sorted daily detail instead of pulling the whole market."""
    meta = table.get("metadata") or {}
    pages = int(meta.get("pagecount") or 1)
    lo, hi, budget = 1, max(1, pages), _max_search_pages()
    while lo <= hi and budget > 0:
        mid = (lo + hi) // 2
        budget -= 1
        rows = _szse_table(date, page=mid, table=2).get("data") or []
        if not rows:
            return None
        codes = [str(row.get("zqdm") or "") for row in rows]
        for row in rows:
            if str(row.get("zqdm") or "") == code:
                return row
        if code < codes[0]:
            hi = mid - 1
        elif code > codes[-1]:
            lo = mid + 1
        else:
            return None          # inside the page range but not present
    return None


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #

def _szse_rjye(row: dict, table: int) -> tuple[Optional[float], str]:
    """Value in 元 for 融券余额, with the unit factor inferred from the vendor identity."""
    raw = _num(row.get(SZSE_RJYE_FIELD))
    if raw is None:
        return None, ""
    total, financing = _num(row.get("jrrzrjye")), _num(row.get("jrrzye"))
    if total is not None and financing is not None:
        # Compare in the column's own unit space (亿元) with a 0.05亿 tolerance for rounding.
        for factor, label in ((1e4, "万元"), (1e8, "亿元")):
            if abs(financing + raw * factor / 1e8 - total) <= 0.05:
                return raw * factor, f"inferred_from_identity:{label}"
    default = SZSE_RJYE_TABLE_DEFAULT[table]
    return raw * default, f"table_default:{'亿元' if default == 1e8 else '万元'}"


def _build(row: dict, *, fields: dict, posture, url: str, research_object: str,
           family: str, date: str, as_of: str, market_scope: str,
           provenance_extra: dict, requested_date: Optional[str],
           rjye_table: Optional[int] = None) -> list[CanonicalRecord]:
    records = []
    for vendor_field, (metric, unit, factor) in fields.items():
        provenance = {"vendorField": vendor_field, "tradeDate": date,
                      "requestedDate": requested_date or date}
        provenance.update(provenance_extra)
        if vendor_field == SZSE_RJYE_FIELD and rjye_table:
            value, basis = _szse_rjye(row, rjye_table)
            if value is None:
                continue
            provenance["vendorValue"] = row.get(vendor_field)
            provenance["unitBasis"] = basis
            provenance["vendorUnit"] = "CNY_10000" if "万元" in basis else "CNY_100m"
        else:
            raw = _num(row.get(vendor_field))
            if raw is None:
                continue
            value = raw * factor
            provenance["vendorValue"] = row.get(vendor_field)
        records.append(CanonicalRecord(
            family=family, research_object=research_object, market_scope=market_scope,
            metric=metric, value=value, unit=unit,
            currency="CNY" if unit == "CNY" else None,
            period=date, period_type="point_in_time", as_of_date=as_of, source_date=date,
            posture=posture, url_or_path=url, provenance=provenance,
        ))
    return records


def _candidate_dates(date: Optional[str]) -> list[str]:
    """Requested date first, then walk back; the exchanges publish on a lag."""
    start = _dt.date.fromisoformat(date) if date else _dt.date.today()
    return [(start - _dt.timedelta(days=offset)).isoformat()
            for offset in range(_max_backfill_days() + 1)]


def _pick_latest(rows: list[dict], date_field: str, requested: Optional[str]):
    dated = [(str(row.get(date_field) or ""), row) for row in rows]
    dated = [(d, row) for d, row in dated if d]
    if not dated:
        return None, requested or ""
    raw, row = max(dated, key=lambda item: item[0])
    return row, _iso(raw) or (requested or "")


def _compact(date: Optional[str]) -> str:
    return (date or _dt.date.today().isoformat()).replace("-", "")


def _iso(raw) -> str:
    text = str(raw or "")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return ""


def _num(value):
    if value is None or isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
