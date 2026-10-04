"""Yahoo Finance v8 chart adapter -> canonical ``market_price``.

Keyless. This is a reproducible **L5 market_pricing** substrate, NOT a
fundamentals fact source (arch doc §7). The full OHLCV history is returned as a
bulk ``series`` (written as a side CSV); only a few snapshot claims (last close,
52-week range, last volume) become evidence-log rows.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional

from .. import cn_symbols, net
from ..canonical import POSTURES, CanonicalRecord, FetchResult

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
SERIES_COLUMNS = ["date", "open", "high", "low", "close", "adjclose", "volume"]


def fetch_market_price(
    symbol: str,
    *,
    as_of: Optional[str] = None,
    market_scope: str = "US",
    range_: str = "1y",
    interval: str = "1d",
) -> FetchResult:
    as_of = as_of or _dt.date.today().isoformat()
    # A bare A-share code needs its exchange suffix, and the suffix is mechanically derivable, so
    # deriving it beats making the caller learn it. A Yahoo 404 names only the URL, which does not
    # say "you forgot .SS". Anything that is not a bare six-digit A-share code is left untouched.
    symbol = cn_symbols.to_yahoo_symbol(symbol)
    url = CHART_URL.format(symbol=symbol) + f"?interval={interval}&range={range_}"
    chart = net.get_json(url).get("chart", {})
    if chart.get("error"):
        raise net.FetchError(f"technical_source_gap: Yahoo error for {symbol}: {chart['error']}")
    result = chart.get("result")
    if not result:
        raise net.FetchError(f"technical_source_gap: empty chart for {symbol}")

    r = result[0]
    meta = r.get("meta", {})
    ts = r.get("timestamp") or []
    ind = r.get("indicators", {})
    quote = (ind.get("quote") or [{}])[0]
    adj = (ind.get("adjclose") or [{}])[0].get("adjclose")

    rows = _series_rows(ts, quote, adj)
    if not rows:
        raise net.FetchError(f"technical_source_gap: no usable bars for {symbol}")

    currency = meta.get("currency")
    last_date = rows[-1]["date"]
    posture = POSTURES["yahoo_chart"]
    sym = symbol.upper()

    # The exchange timezone is the field that makes a relative date resolvable, and the
    # protocol requires it: "today" for 005930.KS is an Asia/Seoul date, not the user's or
    # New York's. A price claim whose market timezone is unrecorded cannot be re-checked for
    # freshness without re-deriving it from the ticker suffix, so it travels with the claim.
    tz_name = meta.get("exchangeTimezoneName")
    tz_offset = meta.get("gmtoffset")
    market_note = {
        "exchange": meta.get("fullExchangeName"),
        "exchangeCode": meta.get("exchangeName"),
        "exchangeTimezoneName": tz_name,
        "gmtoffsetSeconds": tz_offset,
        "sessionNote": ("source_date is the exchange's own session date; resolving a relative "
                        "date for this instrument must use exchangeTimezoneName"),
        "range": range_,
        "interval": interval,
    }

    def mp(metric, value, unit):
        if value is None:
            return None
        return CanonicalRecord(
            family="market_price", research_object=sym, market_scope=market_scope,
            metric=metric, value=value, unit=unit, currency=currency if unit == currency else None,
            period=last_date, period_type="point_in_time", as_of_date=as_of,
            source_date=last_date, posture=posture, url_or_path=url,
            provenance=dict(market_note),
        )

    last_close = meta.get("regularMarketPrice")
    if last_close is None:
        last_close = rows[-1]["close"]
    records = [
        mp("last_close", last_close, currency or "USD"),
        mp("fifty_two_week_high", meta.get("fiftyTwoWeekHigh"), currency or "USD"),
        mp("fifty_two_week_low", meta.get("fiftyTwoWeekLow"), currency or "USD"),
        mp("last_volume", meta.get("regularMarketVolume") or rows[-1]["volume"], "shares"),
    ]
    records = [r_ for r_ in records if r_ is not None]

    series = {"name": f"market_price-{sym}", "columns": SERIES_COLUMNS, "rows": rows}
    return FetchResult(records, series=series)


def _series_rows(ts: list, quote: dict, adj) -> list[dict]:
    opens, highs = quote.get("open") or [], quote.get("high") or []
    lows, closes = quote.get("low") or [], quote.get("close") or []
    vols = quote.get("volume") or []
    rows = []
    for i, t in enumerate(ts):
        close = _at(closes, i)
        if close is None:  # skip non-trading / null bars
            continue
        rows.append({
            "date": _dt.datetime.utcfromtimestamp(t).date().isoformat(),
            "open": _at(opens, i), "high": _at(highs, i), "low": _at(lows, i),
            "close": close, "adjclose": _at(adj or [], i), "volume": _at(vols, i),
        })
    return rows


def _at(seq, i):
    return seq[i] if i < len(seq) else None
