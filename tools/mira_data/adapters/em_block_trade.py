"""大宗交易 (A-share block trades) adapter -> canonical ``market_price`` (L5 relay).

Block trades are a standard A-share lens: a negotiated off-exchange print, its discount or
premium to the close, and who bought from whom. The relay carries both the daily detail and a
per-stock aggregate with the post-trade drift, verified live 2026-10-04:

- ``RPT_DATA_BLOCKTRADE`` — 682,950 rows; one row per print with price, volume, amount, 溢价率,
  buyer and seller seat and the close it is measured against.
- ``RPT_BLOCKTRADE_STA`` — 178,918 rows; one row per stock per session with deal count, total
  volume/amount, average price, and **D1/D5/D10/D20 后市涨跌** (the drift after the print).

**Units were established by arithmetic, not by trusting a label.** ``DEAL_PRICE × DEAL_VOLUME``
reproduces ``DEAL_AMT`` to within rounding on every probed row (思源电气 120.46 × 465,200 =
56,037,992 against a reported 56,032,600), so volume is **shares** and amount is **yuan** — not
the 手/万元 a quarterly-report reader would assume.

**The one trap that would silently mislead:** ``PREMIUM_RATIO`` is a **ratio, not a percent**
(0.001390820584 for 华夏创成长ETF, whose block print sat 0.14% above a 0.719 close). Publishing it
as a percent would report a 0.14% premium as 0.0014%, and 博硕科技's −0.135181 would read as
−0.14% instead of the −13.5% it is. The adapter converts to percent and says so.

Tier is **L5**: this is a relay, and the exchanges publish the same prints.
"""

from __future__ import annotations

import datetime as _dt
import urllib.parse
from typing import Optional

from .. import config, net
from ..canonical import POSTURES, CanonicalRecord, FetchResult

EM_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
DETAIL_REPORT = "RPT_DATA_BLOCKTRADE"
STATS_REPORT = "RPT_BLOCKTRADE_STA"
ENDPOINT = "eastmoney://block-trade"     # no placeholder: REPORT is recorded per event instead
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Referer": "https://data.eastmoney.com/dzjy/dzjy_mrmx.html",
}
DETAIL_COLUMNS = ["tradeDate", "code", "name", "dealPrice", "premiumPercent", "volume",
                  "amount", "closePrice", "buyer", "seller"]
STATS_COLUMNS = ["tradeDate", "code", "name", "dealNum", "volume", "amount", "averagePrice",
                 "premiumPercent", "changePercent", "d1", "d5", "d10", "d20"]
DEFAULT_LIMIT = 200
TIER_NOTE = (
    "L5 aggregator relay of the exchanges' own block-trade prints; prefer exchange_announcements "
    "when the question is what was disclosed")


def fetch_block_trades(
    code: Optional[str] = None,
    *,
    date: Optional[str] = None,
    view: str = "detail",
    as_of: Optional[str] = None,
    market_scope: str = "CN",
    max_items: Optional[int] = None,
) -> FetchResult:
    """Block trades for one session, per print (``detail``) or per stock (``stats``).

    ``code`` narrows to one A-share code (bare, e.g. ``002028``); without it the session's
    trades are returned, largest amount first, which is the useful default because a session
    carries hundreds of prints and the biggest ones are the informative ones.
    """
    as_of = as_of or _dt.date.today().isoformat()
    which = (view or "detail").strip().lower()
    if which not in {"detail", "stats"}:
        raise net.FetchError(
            f"block_trade_view_gap: {view!r} is not a view; this adapter reads detail (one row "
            "per print) or stats (one row per stock per session, with the post-trade drift)")
    limit = max(1, min(1000, int(max_items or _setting("MIRA_BLOCK_TRADE_LIMIT",
                                                       DEFAULT_LIMIT))))
    report = DETAIL_REPORT if which == "detail" else STATS_REPORT

    params = {
        "reportName": report, "columns": "ALL", "pageNumber": "1", "pageSize": str(limit),
        "sortColumns": "TRADE_DATE,DEAL_AMT" if which == "detail" else "TRADE_DATE,DEAL_AMT",
        "sortTypes": "-1,-1", "source": "WEB", "client": "WEB",
    }
    clauses = []
    if date:
        clauses.append(f"(TRADE_DATE='{_iso(date)}')")
    if code:
        clauses.append(f"(SECURITY_CODE=\"{str(code).strip()}\")")
    if clauses:
        params["filter"] = "".join(clauses)

    payload = net.get_json(EM_URL + "?" + urllib.parse.urlencode(params),
                           headers=HEADERS, retries=2, backoff=1.5)
    rows = ((payload.get("result") or {}).get("data") or []) if isinstance(payload, dict) else []
    if not rows:
        detail = f" for {code}" if code else ""
        day = f" on {date}" if date else ""
        raise net.FetchError(
            f"block_trade_source_gap: no {which} row{detail}{day}. Block trades are a negotiated "
            "session event, so a code that did not trade in blocks that session legitimately has "
            "none - widen the date rather than concluding the source is broken")

    posture = POSTURES["em_block_trade"]
    records, series_rows = [], []
    for row in rows:
        day = _day(row.get("TRADE_DATE"))
        symbol = str(row.get("SECURITY_CODE") or "").strip()
        name = str(row.get("SECURITY_NAME_ABBR") or "").strip()
        if not day or not symbol:
            continue
        # The ratio is converted to percent here, once, so no reader has to know the unit.
        premium_ratio = _num(row.get("PREMIUM_RATIO"))
        premium_pct = None if premium_ratio is None else premium_ratio * 100.0
        if which == "detail":
            volume = _num(row.get("DEAL_VOLUME"))
            amount = _num(row.get("DEAL_AMT"))
            price = _num(row.get("DEAL_PRICE"))
            series_rows.append({
                "tradeDate": day, "code": symbol, "name": name, "dealPrice": price,
                "premiumPercent": premium_pct, "volume": volume, "amount": amount,
                "closePrice": _num(row.get("CLOSE_PRICE")),
                "buyer": row.get("BUYER_NAME"), "seller": row.get("SELLER_NAME"),
            })
            records.append(CanonicalRecord(
                family="market_price", research_object=symbol, market_scope=market_scope,
                metric="block_trade_amount", value=amount or 0.0, unit="CNY",
                period=day, period_type="point_in_time", as_of_date=as_of, source_date=day,
                posture=posture, url_or_path=ENDPOINT,
                claim_text=(f"大宗交易 {day} {name}（{symbol}）成交 {_fmt(amount)} 元 / "
                            f"{_fmt(volume)} 股，价 {price}，"
                            f"较收盘 {_fmt(premium_pct, 2)}%"),
                provenance={
                    "view": "detail", "tradeDate": day, "code": symbol, "name": name,
                    "dealPrice": price, "dealVolumeShares": volume, "dealAmountYuan": amount,
                    "closePrice": _num(row.get("CLOSE_PRICE")),
                    "premiumPercent": premium_pct, "premiumRatioAsReturned": premium_ratio,
                    "buyerSeat": row.get("BUYER_NAME"), "sellerSeat": row.get("SELLER_NAME"),
                    "tradeUnitCode": row.get("TRADE_UNIT"),
                    "unitBasis": ("volume and amount were established by arithmetic: "
                                  "DEAL_PRICE x DEAL_VOLUME reproduces DEAL_AMT on every probed "
                                  "row, so in the DETAIL report volume is SHARES and amount is "
                                  "YUAN (not 手/万元). The stats report uses 万股/万元 instead, "
                                  "so do not compare the two views' raw numbers directly"),
                    "premiumUnitNote": ("the vendor returns PREMIUM_RATIO as a RATIO - "
                                        "0.001390820584 is a 0.14% premium, and -0.135181 is "
                                        "-13.5%; it is converted to percent here so it cannot be "
                                        "published as 0.0014%"),
                    "tierBasis": TIER_NOTE,
                },
            ))
        else:
            # THE TWO VIEWS USE DIFFERENT UNITS, which is this dataset's real trap.
            # In the detail report DEAL_VOLUME/DEAL_AMT are raw shares and yuan. In the stats
            # report VOLUME is 万股 and DEAL_AMT is 万元, proven by AMT/VOL == AVERAGE_PRICE on
            # every probed row (思源电气 8705.06 / 72.27 = 120.4519, its average price). Reading
            # the stats figures as raw would understate a 56,032,600-yuan trade as 5,603,260 --
            # an off-by-10,000 error that still looks like a plausible block size.
            amount = _scaled(row.get("DEAL_AMT"))
            series_rows.append({
                "tradeDate": day, "code": symbol, "name": name,
                "dealNum": _num(row.get("DEAL_NUM")), "volume": _scaled(row.get("VOLUME")),
                "amount": amount, "averagePrice": _num(row.get("AVERAGE_PRICE")),
                "premiumPercent": premium_pct, "changePercent": _num(row.get("CHANGE_RATE")),
                "d1": _num(row.get("D1_CLOSE_ADJCHRATE")), "d5": _num(row.get("D5_CLOSE_ADJCHRATE")),
                "d10": _num(row.get("D10_CLOSE_ADJCHRATE")),
                "d20": _num(row.get("D20_CLOSE_ADJCHRATE")),
            })
            records.append(CanonicalRecord(
                family="market_price", research_object=symbol, market_scope=market_scope,
                metric="block_trade_amount", value=amount or 0.0, unit="CNY",
                period=day, period_type="point_in_time", as_of_date=as_of, source_date=day,
                posture=posture, url_or_path=ENDPOINT,
                claim_text=(f"大宗交易 {day} {name}（{symbol}）{_fmt(_num(row.get('DEAL_NUM')))} 笔，"
                            f"成交 {_fmt(amount)} 元，均价 {_fmt(_num(row.get('AVERAGE_PRICE')))}，"
                            f"较收盘 {_fmt(premium_pct, 2)}%"),
                provenance={
                    "view": "stats", "tradeDate": day, "code": symbol, "name": name,
                    "dealNum": _num(row.get("DEAL_NUM")), "volume": _scaled(row.get("VOLUME")),
                    "amountYuan": amount, "averagePrice": _num(row.get("AVERAGE_PRICE")),
                    "volumeAsReturned": _num(row.get("VOLUME")),
                    "amountAsReturned": _num(row.get("DEAL_AMT")),
                    "unitBasis": ("VOLUME is 万股 and DEAL_AMT is 万元 in THIS report "
                                  "(AMT/VOL reproduces AVERAGE_PRICE), unlike the detail "
                                  "report where both are raw; both are scaled to raw "
                                  "shares and yuan here and the vendor values are kept "
                                  "alongside so the scaling is checkable"),
                    "closePrice": _num(row.get("CLOSE_PRICE")),
                    "premiumPercent": premium_pct, "premiumRatioAsReturned": premium_ratio,
                    "changePercent": _num(row.get("CHANGE_RATE")),
                    "forwardReturnD1": _num(row.get("D1_CLOSE_ADJCHRATE")),
                    "forwardReturnD5": _num(row.get("D5_CLOSE_ADJCHRATE")),
                    "forwardReturnD10": _num(row.get("D10_CLOSE_ADJCHRATE")),
                    "forwardReturnD20": _num(row.get("D20_CLOSE_ADJCHRATE")),
                    "unlimitedAShares": _num(row.get("UNLIMITED_A_SHARES")),
                    "forwardReturnNote": ("D1/D5/D10/D20 are the vendor's own post-trade drift "
                                          "figures; they are reported, not computed here, and a "
                                          "null means the horizon had not elapsed at read time"),
                    "unitBasis": ("VOLUME is 万股 and DEAL_AMT is 万元 in THIS report "
                                  "(AMT/VOL reproduces AVERAGE_PRICE), unlike the detail "
                                  "report where both are raw; both are scaled to raw "
                                  "shares and yuan here and the vendor values are kept "
                                  "alongside so the scaling is checkable"),
                    "tierBasis": TIER_NOTE,
                },
            ))
    if not records:
        raise net.FetchError(
            f"block_trade_source_gap: {report} returned {len(rows)} rows but none carried a "
            "usable code and trade date")
    columns = DETAIL_COLUMNS if which == "detail" else STATS_COLUMNS
    series = {"name": f"block-trade-{which}", "columns": columns, "rows": series_rows}
    return FetchResult(records, series=series)


def _iso(text: str) -> str:
    raw = str(text).strip().replace("/", "-")
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw


def _setting(name: str, default: int) -> int:
    try:
        return int((config.get(name) or str(default)).strip())
    except ValueError:
        return default


def _scaled(value):
    """万元/万股 -> raw yuan/shares. The stats report uses the 万 scale."""
    number = _num(value)
    return None if number is None else number * 10000.0


def _day(value) -> str:
    text = str(value or "")
    return text[:10] if len(text) >= 10 and text[4] == "-" else ""


def _fmt(value, digits: int = 0) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.0f}" if digits == 0 else f"{value:,.{digits}f}"


def _num(value):
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None
