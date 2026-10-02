"""Northbound **turnover** from the exchanges themselves -> ``market_price`` (L2 official).

Both mainland exchanges publish Stock Connect turnover as an official aggregate statistic, so
this belongs at L2 next to the margin channel rather than with the aggregator relays.

**What does not exist, and is therefore never emitted.** The exchanges stopped disclosing the
northbound buy/sell split on 2024-08-16, so northbound *net flow* has no upstream at all; an
independent project that publishes its source-admission decisions documented the same cut-off
after reviewing the exchange terms. Only gross turnover survives, and turnover is two-way
trading activity - buys and sells counted together - not capital entering or leaving the
mainland market. Every record therefore carries that caveat as data, and no ``net_flow`` metric
exists in this adapter to be mistaken for one.

Verified contracts (probed live 2026-10-02)
-------------------------------------------
SSE ``GET https://query.sse.com.cn/commonSoaQuery.do``::

    sqlId=FW_HGTZL_HGTSCSJ_HGTCJGK_MRTJ & jsonCallBack= & isPagination=false
    -> {"pageHelp": {"data": [{"totalVolume": "526.94", "totalAmount": "1,012.58",
                               "tradeDate": "20260930", "etfTotalAmount": "19.54"}]}}

SZSE ``GET https://www.szse.cn/api/report/ShowReport/data``::

    SHOWTYPE=JSON & CATALOGID=SGT_SGTJYRB & TABKEY=tab1 & txtDate=YYYY-MM-DD
    -> [{"metadata": {"subname": "2026-09-30", ...},
         "data": [{"label": "当日交易总额（亿元人民币）", "total": "1,066.84"},
                  {"label": "当日ETF交易总额（亿元人民币）", "total": "16.12"},
                  {"label": "当日交易总笔数（万笔）", "total": "570.28"}]}]

Two traps: the SZSE date parameter must be the **dashed** ``YYYY-MM-DD`` form (the compact form
returns zero rows while still answering HTTP 200), and without a date the endpoint returns 1500
rows spanning many sessions, which is why this adapter always pins one date and walks back a
bounded number of days when a non-trading day is asked for. Values arrive as strings and
frequently carry thousands separators.
"""

from __future__ import annotations

import datetime as _dt
import re
import urllib.parse
from typing import Optional

from .. import config, net
from ..canonical import POSTURES, CanonicalRecord, FetchResult

SSE_URL = "https://query.sse.com.cn/commonSoaQuery.do"
SSE_SQL_ID = "FW_HGTZL_HGTSCSJ_HGTCJGK_MRTJ"
SZSE_URL = "https://www.szse.cn/api/report/ShowReport/data"
SZSE_CATALOG = "SGT_SGTJYRB"
ENDPOINT = "exchange-northbound://{symbol}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept": "application/json", "Referer": "https://www.sse.com.cn/"}
SZSE_HEADERS = dict(HEADERS, Referer="https://www.szse.cn/")
VENUES = ("SSE", "SZSE", "BOTH")
NET_FLOW_CAVEAT = ("northbound net flow has no upstream: both exchanges stopped publishing the "
                   "buy/sell split on 2024-08-16, so only gross turnover exists, which is "
                   "two-way trading activity rather than capital entering or leaving")
SZSE_LABELS = {"当日交易总额（亿元人民币）": ("northbound_turnover_amount", "CNY_100m"),
               "当日ETF交易总额（亿元人民币）": ("northbound_etf_amount", "CNY_100m"),
               "当日交易总笔数（万笔）": ("northbound_trade_count", "10k_trades")}
MAX_BACKFILL_DAYS = 7


def _backfill_days() -> int:
    try:
        return max(0, min(30, int((config.get("MIRA_NORTHBOUND_MAX_BACKFILL_DAYS")
                                   or str(MAX_BACKFILL_DAYS)).strip())))
    except ValueError:
        return MAX_BACKFILL_DAYS


def fetch_northbound_turnover(
    venue: str = "BOTH",
    *,
    date: Optional[str] = None,
    as_of: Optional[str] = None,
    market_scope: str = "CN",
) -> FetchResult:
    """Official Stock Connect turnover for one session, per requested venue."""
    as_of = as_of or _dt.date.today().isoformat()
    wanted = (venue or "BOTH").strip().upper()
    if wanted not in VENUES:
        raise net.FetchError(f"invalid_venue: {venue!r} (use {'/'.join(VENUES)})")
    names = ["SSE", "SZSE"] if wanted == "BOTH" else [wanted]

    records: list[CanonicalRecord] = []
    errors: list[str] = []
    used_dates: set[str] = set()
    for name in names:
        try:
            found = _sse_records if name == "SSE" else _szse_records
            leg = found(date=date, as_of=as_of, market_scope=market_scope)
        except net.FetchError as exc:
            errors.append(f"{name}: {exc}")
            continue
        records.extend(leg)
        used_dates.update(record.period for record in leg)
    if not records:
        raise net.FetchError("northbound_source_gap: " + "; ".join(errors))
    if len(used_dates) > 1:
        # The two venues do not always publish the same session; say so instead of blending.
        for record in records:
            record.provenance["tradeDateMismatch"] = sorted(used_dates)
    return FetchResult(records)


def _sse_records(*, date: Optional[str], as_of: str,
                 market_scope: str) -> list[CanonicalRecord]:
    posture = POSTURES["sse_northbound"]
    url = SSE_URL + "?" + urllib.parse.urlencode(
        {"jsonCallBack": "", "sqlId": SSE_SQL_ID, "isPagination": "false"})
    payload = net.get_json(url, headers=HEADERS, retries=2, backoff=1.5)
    entries = ((payload.get("pageHelp") or {}).get("data")) or []
    if not entries:
        raise net.FetchError("no turnover row in the response")
    entry = entries[0]
    trade_date = _iso_day(entry.get("tradeDate"))
    if date and trade_date and trade_date != date:
        raise net.FetchError(
            f"the endpoint answered for {trade_date}, not the requested {date} "
            "(it publishes the latest session only)")
    specs = (("northbound_turnover_amount", "totalAmount", "CNY_100m"),
             ("northbound_turnover_volume", "totalVolume", "100m_shares"),
             ("northbound_etf_amount", "etfTotalAmount", "CNY_100m"))
    records = []
    for metric, field, unit in specs:
        value = _number(entry.get(field))
        if value is None:
            continue
        records.append(_record(metric=metric, value=value, unit=unit, venue="SSE",
                               trade_date=trade_date, as_of=as_of, posture=posture,
                               market_scope=market_scope,
                               raw={k: entry.get(k) for k in
                                    ("totalVolume", "totalAmount", "etfTotalAmount",
                                     "tradeDate")},
                               venue_note="沪股通成交概况"))
    if not records:
        raise net.FetchError("the SSE row carried no numeric turnover")
    return records


def _szse_records(*, date: Optional[str], as_of: str,
                  market_scope: str) -> list[CanonicalRecord]:
    posture = POSTURES["szse_northbound"]
    anchor = date or as_of
    start = _dt.date.fromisoformat(anchor)
    last_error = "no session found"
    for offset in range(_backfill_days() + 1):
        day = (start - _dt.timedelta(days=offset)).isoformat()
        url = SZSE_URL + "?" + urllib.parse.urlencode(
            {"SHOWTYPE": "JSON", "CATALOGID": SZSE_CATALOG, "TABKEY": "tab1",
             "txtDate": day})
        payload = net.get_json(url, headers=SZSE_HEADERS, retries=2, backoff=1.5)
        blocks = payload if isinstance(payload, list) else [payload]
        rows = (blocks[0].get("data") if blocks else None) or []
        records = []
        for row in rows:
            label = str(row.get("label") or "").strip()
            spec = SZSE_LABELS.get(label)
            value = _number(row.get("total"))
            if not spec or value is None:
                continue
            records.append(_record(metric=spec[0], value=value, unit=spec[1], venue="SZSE",
                                   trade_date=day, as_of=as_of, posture=posture,
                                   market_scope=market_scope, raw={"label": label,
                                                                   "total": row.get("total")},
                                   venue_note="深股通交易日报"))
        if records:
            if day != anchor:
                for record in records:
                    record.provenance["backfilledFrom"] = anchor
                    record.provenance["usedTradeDate"] = day
            return records
        last_error = f"no published row for {day} (the date parameter must be YYYY-MM-DD)"
    raise net.FetchError(last_error)


def _record(*, metric: str, value: float, unit: str, venue: str, trade_date: str, as_of: str,
            posture, market_scope: str, raw: dict, venue_note: str) -> CanonicalRecord:
    return CanonicalRecord(
        family="market_price", research_object=f"CN_{venue}_NORTHBOUND",
        market_scope=market_scope, metric=metric, value=value, unit=unit,
        period=trade_date, period_type="point_in_time", as_of_date=as_of,
        source_date=trade_date, posture=posture, url_or_path=ENDPOINT.format(symbol=venue),
        currency="CNY" if unit == "CNY_100m" else None,
        claim_text=f"{venue} 北向成交 {metric} = {value} {unit} ({trade_date})",
        provenance={"venue": venue, "vendorReport": venue_note, "tradeDate": trade_date,
                    "netFlowUnavailable": NET_FLOW_CAVEAT,
                    "netFlowDiscontinuedOn": "2024-08-16", "vendorFields": raw,
                    "unitBasis": ("亿元 as published by the exchange; the SSE volume field is "
                                  "in 亿股")},
    )


def _number(value) -> Optional[float]:
    if value is None or isinstance(value, (int, float)):
        return float(value) if value is not None else None
    text = re.sub(r"[,%\s]", "", str(value))
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _iso_day(value) -> str:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text[:10] if re.match(r"\d{4}-\d{2}-\d{2}", text) else ""
