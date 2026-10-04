"""CBOE volatility indices adapter -> canonical ``macro_series`` (L2, keyless).

The exchange that calculates and publishes the volatility indices serves them directly as
CSV, so these are L2 facts rather than a relayed price. Before this channel the only route to
VIX was the Yahoo chart at L5, which yields a *price* — no history depth guarantee, no
provider identity, and no way to reach the rest of the complex.

Nine series are wired, all verified live 2026-10-04::

    VIX     S&P 500 volatility                9,287 rows from 1990
    VIX9D   9-day                               3,961 rows
    VIX3M   3-month                             4,287 rows
    VIX6M   6-month                             4,719 rows
    VVIX    volatility of VIX                   5,118 rows (single column)
    VXN     Nasdaq-100 volatility               4,293 rows
    RVX     Russell 2000 volatility             4,284 rows
    GVZ     gold volatility                     4,285 rows (single column)
    OVX     crude-oil volatility                4,285 rows (single column)

**Two CSV shapes, and assuming one breaks the other.** The VIX family publishes
``DATE,OPEN,HIGH,LOW,CLOSE`` while VVIX/GVZ/OVX publish only ``DATE,<SYMBOL>``. The parser
therefore reads the header and takes the close column when present, else the sole value column —
guessing "column 5" would silently return nothing for the single-column files.

Dates are ``MM/DD/YYYY`` (US format), which must not be read as day/month.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Optional

from .. import config, net
from ..canonical import POSTURES, CanonicalRecord, FetchResult

HISTORY_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{symbol}_History.csv"
ENDPOINT = "cboe://volatility-index/{symbol}"
HEADERS = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")}
SERIES_COLUMNS = ["symbol", "date", "open", "high", "low", "close", "value"]
DEFAULT_TAIL = 30

# symbol -> (what it measures, underlying)
SERIES = {
    "VIX": ("S&P 500 30-day implied volatility", "S&P 500"),
    "VIX9D": ("S&P 500 9-day implied volatility", "S&P 500"),
    "VIX3M": ("S&P 500 3-month implied volatility", "S&P 500"),
    "VIX6M": ("S&P 500 6-month implied volatility", "S&P 500"),
    "VVIX": ("volatility of VIX", "VIX"),
    "VXN": ("Nasdaq-100 implied volatility", "Nasdaq-100"),
    "RVX": ("Russell 2000 implied volatility", "Russell 2000"),
    "GVZ": ("gold ETF implied volatility", "gold"),
    "OVX": ("crude-oil ETF implied volatility", "crude oil"),
}


def fetch_volatility_index(
    symbol: str = "VIX",
    *,
    as_of: Optional[str] = None,
    market_scope: str = "US",
    limit: Optional[int] = None,
) -> FetchResult:
    """Latest observation of one CBOE volatility index, plus the recent window.

    The series is the CBOE's own history file, so the window can be as long as the provider
    publishes (VIX reaches back to 1990); ``limit`` bounds the rows kept for the side CSV.
    """
    as_of = as_of or _dt.date.today().isoformat()
    code = (symbol or "VIX").strip().upper()
    if code not in SERIES:
        raise net.FetchError(
            f"cboe_source_gap: {symbol!r} is not one of the CBOE volatility indices this "
            f"adapter reads ({'/'.join(sorted(SERIES))}). Other CBOE indices exist but are not "
            "claimed here, because each would need its own verified history file")
    described, underlying = SERIES[code]
    tail = max(1, min(5000, int(limit or _setting("MIRA_CBOE_TAIL", DEFAULT_TAIL))))

    url = HISTORY_URL.format(symbol=code)
    raw = net.get(url, headers=HEADERS, retries=2, backoff=1.5)
    rows = _parse_history(raw.decode("utf-8", "replace"), code)
    if not rows:
        raise net.FetchError(
            f"cboe_source_gap: the {code} history file parsed to no usable observation")

    recent = rows[-tail:]
    latest = recent[-1]
    posture = POSTURES["cboe_volatility"]
    record = CanonicalRecord(
        family="macro_series", research_object=f"CBOE_{code}",
        market_scope=market_scope, metric=f"{code.lower()}_close",
        value=latest["value"], unit="index_points", period=latest["date"],
        period_type="point_in_time", as_of_date=as_of, source_date=latest["date"],
        posture=posture, url_or_path=url,
        claim_text=(f"CBOE {code}（{described}，标的 {underlying}）"
                    f"{latest['date']} 收盘 {latest['value']}"),
        provenance={
            "symbol": code, "seriesDescription": described, "underlying": underlying,
            "observationDate": latest["date"],
            "close": latest["value"], "open": latest["open"],
            "high": latest["high"], "low": latest["low"],
            "rowsInFile": len(rows), "rowsKept": len(recent),
            "firstDate": rows[0]["date"], "lastDate": rows[-1]["date"],
            "shapeNote": ("the VIX family publishes DATE,OPEN,HIGH,LOW,CLOSE while VVIX/GVZ/OVX "
                          "publish only DATE,<SYMBOL>; the header decides which column carries "
                          "the value"),
            "dateFormatNote": ("dates in the file are MM/DD/YYYY (US), so they must not be read "
                               "as day/month"),
            "tierBasis": ("the exchange that calculates the index publishes the file, so this is "
                          "an official L2 statistic rather than a relayed price"),
        },
    )
    series = {"name": f"cboe-{code}", "columns": SERIES_COLUMNS, "rows": recent}
    return FetchResult([record], series=series)


def _parse_history(text: str, symbol: str) -> list[dict]:
    """Read the CBOE history CSV in whichever of its two shapes it arrived."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    header = [cell.strip().upper() for cell in lines[0].split(",")]
    try:
        date_at = header.index("DATE")
    except ValueError:
        return []
    close_at = header.index("CLOSE") if "CLOSE" in header else None
    if close_at is None:
        # Single-column shape: DATE,<SYMBOL>. Prefer the symbol's own column, else the last one.
        close_at = header.index(symbol) if symbol in header else len(header) - 1
    idx = {name: header.index(name) for name in ("OPEN", "HIGH", "LOW") if name in header}

    def cell(cells: list[str], at: Optional[int]):
        if at is None or at >= len(cells):
            return None
        return _num(cells[at])

    out: list[dict] = []
    for line in lines[1:]:
        cells = [c.strip() for c in line.split(",")]
        day = _us_date(cells[date_at] if date_at < len(cells) else "")
        value = cell(cells, close_at)
        if not day or value is None:
            continue
        out.append({
            "symbol": symbol, "date": day, "open": cell(cells, idx.get("OPEN")),
            "high": cell(cells, idx.get("HIGH")), "low": cell(cells, idx.get("LOW")),
            "close": value, "value": value,
        })
    out.sort(key=lambda row: row["date"])
    return out


def _us_date(value: str) -> str:
    """``01/02/1990`` -> ``1990-01-02``; US order, so month comes first."""
    match = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", (value or "").strip())
    if not match:
        return ""
    month, day, year = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def _setting(name: str, default: int) -> int:
    try:
        return int((config.get(name) or str(default)).strip())
    except ValueError:
        return default


def _num(value):
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None
