"""龙虎榜 (dragon-tiger list) adapter -> canonical records (L5 relay, licensed vendor CLI).

The daily disclosure of which seats traded a stock hard enough to be published, with the
buy/sell split and the split between institutional and "hot money" seats, plus the per-seat
(hot money) view of what each well-known seat bought.

**Tier, stated plainly.** This is the **L5 vendor relay**, not the exchanges' own
disclosure: the SSE and SZSE publish the same lists, so prefer `exchange_announcements`
(L1/L2) when the question is what was *disclosed*, and treat this as the structured,
queryable view. It is still the right shape for scanning — one call returns every name on a
session with the net figures already computed.

Probed live 2026-10-04 against 2026-09-30, the last session in the vendor's own trading
calendar:

- ``--board-type all`` -> 79 ``stock_items`` (万科A net_value 71,384,559.48, hot_rank 1)
- ``--board-type hot_money`` -> 15 ``hot_money_items`` (佛山系 buying 183,459,255)

Two traps, both measured:

1. **The date must be an A-share trading day and the CLI refuses others outright**
   (``FUYAO_1002``: "date must be an A-share trading day"). A weekend or a National-Day-holiday
   date is a *validation* error, not an empty list — so 2026-09-25 and 2026-10-02 both failed
   before 2026-09-30 worked. Use ``market calendar`` rather than guessing.
2. **An empty result can be legitimate.** Passing a valid session on which nothing qualified
   returns zero rows with ``ok: true``; that is a session-level fact and is labelled as a gap
   with the reason, not silently emitted as "no data".
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional

from .. import config, net
from ..canonical import POSTURES, CanonicalRecord, FetchResult
from .hithink_finance import vendor_json

ENDPOINT = "hithink-finance://special/dragon-tiger"
BOARD_TYPES = ("all", "org", "hot_money")
STOCK_COLUMNS = ["date", "thscode", "ticker", "name", "boardType", "change", "netValue",
                 "netRate", "buyValue", "sellValue", "orgNetValue", "hotMoneyNetValue",
                 "hotRank", "rangeDays"]
HOT_MONEY_COLUMNS = ["date", "seat", "buying", "thscode", "ticker", "name", "netValue",
                     "netRate", "change", "hotRank"]


def fetch_dragon_tiger(
    date: Optional[str] = None,
    *,
    board_type: str = "all",
    as_of: Optional[str] = None,
    market_scope: str = "CN",
    max_items: Optional[int] = None,
) -> FetchResult:
    """One session's 龙虎榜, per stock or per hot-money seat.

    ``board_type`` selects the view: ``all``/``org`` return one row per stock, ``hot_money``
    returns one row per well-known seat with its holdings nested. ``date`` must be a trading
    day; without it the vendor uses its own most recent session.
    """
    as_of = as_of or _dt.date.today().isoformat()
    board = (board_type or "all").strip().lower()
    if board not in BOARD_TYPES:
        raise net.FetchError(
            f"hithink_board_gap: {board_type!r} is not a board type; this adapter reads "
            f"{'/'.join(BOARD_TYPES)} (all/per-stock, org/institutional, hot_money/per-seat)")
    limit = max(1, min(1000, int(max_items or _setting("MIRA_LHB_MAX_ITEMS", 200))))

    cmd = ["special", "dragon-tiger", "--board-type", board]
    if date:
        cmd += ["--date", str(date).strip()]
    data = vendor_json(cmd)
    session = str(data.get("trade_date") or date or as_of)
    stock_items = data.get("stock_items") or []
    seat_items = data.get("hot_money_items") or []
    posture = POSTURES["hithink_dragon_tiger"]

    records: list[dict] = []
    if board == "hot_money":
        rows = _hot_money_rows(seat_items, session)
        for row in rows[:limit]:
            seat = row["seat"]
            records.append(CanonicalRecord(
                family="ownership_short_interest",
                research_object=f"LHB_SEAT_{row['ticker'] or seat}",
                market_scope=market_scope, metric=f"lhb_seat_net_{row['ticker'] or 'unknown'}",
                value=row["netValue"] or 0.0, unit="CNY", period=session,
                period_type="point_in_time", as_of_date=as_of, source_date=session,
                posture=posture, url_or_path=ENDPOINT,
                claim_text=(f"龙虎榜 {session} 席位「{seat}」{row['name'] or row['ticker']} "
                            f"净额 {_fmt(row['netValue'])}"),
                provenance={
                    "boardType": board, "tradeDate": session, "seat": seat,
                    "seatBuying": row["buying"], "thscode": row["thscode"],
                    "netValue": row["netValue"], "netRate": row["netRate"],
                    "change": row["change"],
                    "tierBasis": TIER_NOTE,
                    "seatNote": ("one row per well-known seat, with the stocks it traded "
                                 "nested; the same stock appears under several seats, so these "
                                 "rows do not sum to the per-stock view"),
                },
            ))
    else:
        rows = _stock_rows(stock_items, session, board)
        # The concept tags are joined per stock for provenance; they are the vendor's own
        # taxonomy, so they annotate rather than classify. Keyed by ticker because the row
        # itself stays flat for the side-series CSV.
        tags = {it.get("ticker"): " | ".join(_concepts(it)) for it in stock_items}
        # `org` narrows to the institutional table; the payload is otherwise the same shape.
        if board == "org":
            rows = [r for r in rows if (r["orgNetValue"] or 0) != 0]
        for row in rows[:limit]:
            records.append(CanonicalRecord(
                family="ownership_short_interest",
                research_object=f"LHB_{row['ticker']}",
                market_scope=market_scope, metric="lhb_net_value",
                value=row["netValue"] or 0.0, unit="CNY", period=session,
                period_type="point_in_time", as_of_date=as_of, source_date=session,
                posture=posture, url_or_path=ENDPOINT,
                claim_text=(f"龙虎榜 {session} {row['name']}（{row['ticker']}）"
                            f"净买入 {_fmt(row['netValue'])}，"
                            f"买方 {_fmt(row['buyValue'])} / 卖方 {_fmt(row['sellValue'])}"),
                provenance={
                    "boardType": board, "tradeDate": session, "thscode": row["thscode"],
                    "ticker": row["ticker"], "name": row["name"],
                    "change": row["change"], "netValue": row["netValue"],
                    "netRate": row["netRate"], "buyValue": row["buyValue"],
                    "sellValue": row["sellValue"], "orgNetValue": row["orgNetValue"],
                    "hotMoneyNetValue": row["hotMoneyNetValue"],
                    "hotRank": row["hotRank"], "rangeDays": row["rangeDays"],
                    # The evidence-log schema holds flat scalars, so the tag list is joined;
                    # a nested list is rejected by the CSV writer rather than stored.
                    "concepts": tags.get(row["ticker"], ""),
                    "tierBasis": TIER_NOTE,
                    "conceptNote": ("concept tags come from the vendor's own taxonomy and are "
                                    "not an official classification"),
                },
            ))

    if not records:
        raise net.FetchError(
            f"lhb_source_gap: the {board} board returned no row for {session}: this is an EMPTY "
            "SESSION rather than a failure (a valid session on which nothing qualified returns "
            "ok:true with empty lists), but it is reported instead of emitting zero claims. It "
            "is a different outcome from a non-trading date, which the CLI refuses with its own "
            "validation error before reaching here")
    series = ({"name": "dragon-tiger-seats", "columns": HOT_MONEY_COLUMNS,
               "rows": _hot_money_rows(seat_items, session)}
              if board == "hot_money" else
              {"name": "dragon-tiger", "columns": STOCK_COLUMNS,
               "rows": _stock_rows(stock_items, session, board)})
    return FetchResult(records, series=series)


TIER_NOTE = (
    "L5 vendor relay of the exchanges' own daily 龙虎榜 disclosure; the SSE/SZSE published the "
    "same lists, so prefer exchange_announcements when the question is what was disclosed")


def _concepts(item: dict) -> list[str]:
    return [c.get("name") for c in (item.get("concept_list") or []) if isinstance(c, dict)]


def _stock_rows(items: list, session: str, board: str) -> list[dict]:
    """One flat row per stock.

    The row keys must match ``STOCK_COLUMNS`` exactly: this same list becomes the side-series
    CSV, whose writer rejects a dict carrying a field the header does not declare. So the
    concept tags stay out of the row and are joined into provenance instead.
    """
    rows = []
    for item in items:
        rows.append({
            "date": session, "thscode": item.get("thscode"), "ticker": item.get("ticker"),
            "name": item.get("name"), "boardType": board,
            "change": _num(item.get("change")), "netValue": _num(item.get("net_value")),
            "netRate": _num(item.get("net_rate")), "buyValue": _num(item.get("buy_value")),
            "sellValue": _num(item.get("sell_value")),
            "orgNetValue": _num(item.get("org_net_value")),
            "hotMoneyNetValue": _num(item.get("hot_money_net_value")),
            "hotRank": _num(item.get("hot_rank")), "rangeDays": _num(item.get("range_days")),
        })
    return rows


def _hot_money_rows(items: list, session: str) -> list[dict]:
    """Flatten the per-seat payload: one row per (seat, stock) pair."""
    rows = []
    for seat in items:
        name = seat.get("name")
        buying = _num(seat.get("buying"))
        for entry in (seat.get("rows") or []):
            rows.append({
                "date": session, "seat": name, "buying": buying,
                "thscode": entry.get("thscode"), "ticker": entry.get("ticker"),
                "name": entry.get("name"), "netValue": _num(entry.get("net_value")),
                "netRate": _num(entry.get("net_rate")), "change": _num(entry.get("change")),
                "hotRank": _num(entry.get("hot_rank")),
            })
    return rows


def _setting(name: str, default: int) -> int:
    try:
        return int((config.get(name) or str(default)).strip())
    except ValueError:
        return default


def _fmt(value) -> str:
    """Format an amount, or say so when the vendor left it absent.

    Formatting a None with a numeric spec would raise, and defaulting it to zero would turn
    "the vendor did not report this" into "this was zero" — two very different claims.
    """
    return "n/a" if value is None else f"{value:,.0f}"


def _num(value):
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None
