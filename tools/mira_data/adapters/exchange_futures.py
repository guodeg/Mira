"""Official futures member-position rankings (L2) from SHFE and CZCE.

This is the exchange-published counterpart to the vendor's ``futures.*`` reads in the
hithink CLI. The vendor carries these at L5 (aggregated relay); the exchange is the
**controlling source for its own market**, so a ranking read from here is a genuine L2 fact
and can anchor a positioning claim that the relayed copy cannot.

Two venues, two transports, one shape:

- **SHFE** publishes a JSON daily file per session:
  ``/data/tradedata/future/dailydata/pm{YYYYMMDD}.dat`` (verified live 2026-10-03, 528 KB,
  1,700 rows). Each row carries a contract (or ``cuall`` for the variety aggregate), a rank,
  and up to three member slots with their volumes, long positions and short positions — the
  three ranked tables are published side by side rather than as separate files.
- **CZCE** publishes a pipe-delimited text file per session:
  ``/cn/DFSStaticFiles/Future/{YYYY}/{YYYYMMDD}/FutureDataHolding.txt`` (verified live,
  440 KB, 2,962 lines). It is one table per variety, each with a ``品种：… 日期：…`` header,
  and rows of ``rank|member|volume|Δ|member|long|Δ|member|short|Δ``.

Scope, stated rather than implied: this module provides **member rankings only**. Warehouse
receipts (仓单) and basis are not here — SHFE's stock file was probed at five plausible paths
and is not at any of them, so it needs its own investigation rather than a guessed URL.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Optional

from .. import config, net
from ..canonical import POSTURES, CanonicalRecord, FetchResult

SHFE_PM_URL = "https://www.shfe.com.cn/data/tradedata/future/dailydata/pm{date}.dat"
CZCE_HOLDING_URL = ("http://www.czce.com.cn/cn/DFSStaticFiles/Future/{year}/{date}/"
                    "FutureDataHolding.txt")
HEADERS = {"User-Agent": "Mozilla/5.0"}
MAX_BACKFILL_DAYS = 10          # a session may not have published yet when asked
DEFAULT_RANK_LIMIT = 20         # exchanges publish top-20 per table

# SHFE's row is the three ranked tables side by side; slot 1/2/3 are volume/long/short order
# only because the file ships them that way (CJ = 成交量, CJ1/CJ2/CJ3 with their _CHG deltas).
SHFE_SLOTS = ("1", "2", "3")


def _date_param(day: Optional[str], as_of: str) -> str:
    day = day or as_of
    return _dt.date.fromisoformat(day).strftime("%Y%m%d")


def _session_dates(day: Optional[str], as_of: str, backfill: int) -> list[str]:
    """The requested session first, then earlier ones, because publication lags."""
    start = _dt.date.fromisoformat(day or as_of)
    return [(start - _dt.timedelta(days=offset)).isoformat() for offset in range(max(1, backfill))]


def fetch_member_rankings(
    variety: str,
    *,
    venue: str = "SHFE",
    date: Optional[str] = None,
    as_of: Optional[str] = None,
    market_scope: str = "CN",
    rank_limit: Optional[int] = None,
) -> FetchResult:
    """Top ranked members for one variety, from the exchange that publishes it.

    ``venue`` is ``SHFE`` or ``CZCE``. ``date`` is ``YYYY-MM-DD``; when the requested session
    has not published, the adapter walks back a bounded number of days and records **both**
    the requested and the used date, so a caller never mistakes one session for another.
    """
    as_of = as_of or _dt.date.today().isoformat()
    venue = (venue or "SHFE").strip().upper()
    limit = max(1, min(20, int(rank_limit or _setting("MIRA_FUTURES_RANK_LIMIT",
                                                      DEFAULT_RANK_LIMIT))))
    if venue not in {"SHFE", "CZCE"}:
        raise net.FetchError(
            f"futures_venue_gap: {venue!r} is not wired; this adapter reads SHFE and CZCE "
            "member rankings (DCE answers HTTP 412 to a scripted client, CFFEX serves HTML "
            "where a CSV is expected, and GFEX answered HTTP 520 when probed)")

    errors: list[str] = []
    for candidate in _session_dates(date, as_of, MAX_BACKFILL_DAYS):
        try:
            if venue == "SHFE":
                records = _shfe_records(variety, candidate, limit, as_of, market_scope, date)
            else:
                records = _czce_records(variety, candidate, limit, as_of, market_scope, date)
        except net.FetchError as exc:
            errors.append(f"{candidate}: {exc}")
            continue
        if records:
            return FetchResult(records)
        errors.append(f"{candidate}: no rows for {variety}")
    raise net.FetchError(
        f"futures_source_gap: no {venue} member ranking for {variety} in the last "
        f"{MAX_BACKFILL_DAYS} sessions; tried {errors[:3]}")


def _setting(name: str, default: int) -> int:
    try:
        return int((config.get(name) or str(default)).strip())
    except ValueError:
        return default


def _shfe_records(variety, session, limit, as_of, market_scope, requested) -> list:
    url = SHFE_PM_URL.format(date=session.replace("-", ""))
    try:
        payload = net.get_json(url, headers=HEADERS, retries=1, backoff=1.0)
    except net.FetchError as exc:
        raise net.FetchError(f"shfe fetch failed ({exc})") from exc
    rows = payload.get("o_cursor") or []
    if not rows:
        return []
    wanted = variety.strip().lower()
    posture = POSTURES["shfe_member_rank"]
    records = []
    for row in rows:
        instrument = str(row.get("INSTRUMENTID") or "").strip()
        # Match the variety exactly: `cuall` is its aggregate, `cu2610` one of its contracts.
        # A bare ``startswith`` is wrong here — every coded instrument beginning with the two
        # letters matched, so a `cu` request swallowed 331 rows across unrelated series.
        low = instrument.lower()
        if low != f"{wanted}all" and re.fullmatch(rf"{re.escape(wanted)}\d{{3,4}}", low) is None:
            continue
        rank = row.get("RANK")
        if not isinstance(rank, int) or rank < 1 or rank > limit:
            continue
        volume = _num_cn(row.get("CJ1"))
        long_pos = _num_cn(row.get("CJ2"))
        short_pos = _num_cn(row.get("CJ3"))
        member = str(row.get("PARTICIPANTABBR1") or "").strip()
        if not member or volume is None:
            continue
        records.append(CanonicalRecord(
            family="ownership_short_interest", research_object=instrument.upper(),
            market_scope=market_scope, metric="member_rank",
            value=float(rank), unit="rank", period=session, period_type="point_in_time",
            as_of_date=as_of, source_date=session, posture=posture,
            url_or_path=url, claim_text=(
                f"SHFE {session} {instrument} 第{rank}名 {member}："
                f"成交量 {volume:,.0f} 手，持买单量 {_fmt(long_pos)}，持卖单量 {_fmt(short_pos)}"),
            provenance={
                "venue": "SHFE", "variety": wanted, "instrument": instrument,
                "rank": rank, "member": member,
                "volume": volume, "longPositions": long_pos, "shortPositions": short_pos,
                "volumeChange": _num_cn(row.get("CJ1_CHG")),
                "longChange": _num_cn(row.get("CJ2_CHG")),
                "shortChange": _num_cn(row.get("CJ3_CHG")),
                "productName": row.get("PRODUCTNAME"),
                "reportDate": payload.get("report_date"),
                "requestedDate": requested or session, "usedDate": session,
                "units": "lots (手); the exchange publishes volume, long and short side by side",
                "aggregateNote": ("an `{variety}all` instrument is the variety aggregate, a "
                                  "coded instrument like cu2610 is one contract"),
            },
        ))
    return records


CZCE_HEADER_RE = re.compile(r"品种[：:]\s*(\S+?)\s+日期[：:]\s*(\d{4}-\d{2}-\d{2})")


def _czce_code(variety_label: str) -> str:
    """``苹果AP`` -> ``AP``. The label is a Chinese name glued to the ticker."""
    match = re.search(r"([A-Za-z]+)\s*$", variety_label or "")
    return match.group(1).upper() if match else (variety_label or "").upper()


def _czce_records(variety, session, limit, as_of, market_scope, requested) -> list:
    url = CZCE_HOLDING_URL.format(year=session[:4], date=session.replace("-", ""))
    raw = net.get(url, headers=HEADERS, retries=1, backoff=1.0)
    # The file is UTF-8; decoding it as GBK produced mojibake when first probed, which is why
    # the codec is pinned here rather than left to the platform default.
    text = raw.decode("utf-8", "replace")
    wanted = variety.strip().upper()
    posture = POSTURES["czce_member_rank"]
    records = []
    current_variety = ""
    for line in text.splitlines():
        header = CZCE_HEADER_RE.search(line)
        if header:
            current_variety = header.group(1).strip()
            continue
        if not current_variety or "|" not in line:
            continue
        # CZCE labels a table `品种：苹果AP` — the Chinese name followed by the ticker with no
        # separator, so the requested code is the TRAILING ascii run, not a prefix. Matching on
        # a prefix found nothing at all (every header starts with the Chinese name).
        code = _czce_code(current_variety)
        if wanted and code != wanted:
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 10:
            continue
        try:
            rank = int(cells[0])
        except ValueError:
            continue
        if rank < 1 or rank > limit:
            continue
        records.append(CanonicalRecord(
            family="ownership_short_interest", research_object=current_variety,
            market_scope=market_scope, metric="member_rank",
            value=float(rank), unit="rank", period=session, period_type="point_in_time",
            as_of_date=as_of, source_date=session, posture=posture, url_or_path=url,
            claim_text=(f"CZCE {session} {current_variety} 第{rank}名 "
                        f"{cells[1]}：成交量 {cells[2]} 手，持买单量 {cells[5]}，"
                        f"持卖单量 {cells[8]}"),
            provenance={
                "venue": "CZCE", "variety": current_variety, "rank": rank,
                "volumeMember": cells[1], "volume": _num_cn(cells[2]),
                "volumeChange": _num_cn(cells[3]),
                "longMember": cells[4], "longPositions": _num_cn(cells[5]),
                "longChange": _num_cn(cells[6]),
                "shortMember": cells[7], "shortPositions": _num_cn(cells[8]),
                "shortChange": _num_cn(cells[9]),
                "requestedDate": requested or session, "usedDate": session,
                "units": "lots (手)",
                "sideBySideNote": ("the three ranked tables (volume / long / short) are "
                                   "published in one row and the members may differ per "
                                   "table, so each side keeps its own member name"),
            },
        ))
    return records


def _num_cn(value):
    """Exchange files mix ``39,292``, ``-3,850`` and empty cells."""
    if value is None:
        return None
    text = re.sub(r"[,，\s]", "", str(value))
    if text in {"", "-", "--", "—"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _fmt(value) -> str:
    return f"{value:,.0f}" if isinstance(value, float) else "n/a"
