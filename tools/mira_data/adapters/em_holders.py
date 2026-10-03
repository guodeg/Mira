"""十大股东 / 十大流通股东 from the Eastmoney datacenter relay (L5).

The A-share market-structure gate asks for the shareholder-structure input, and the two
tables it names are the issuer's own 前十名股东 and 前十名流通股东 tables inside the
periodic report. This module reads the relay's structured copy of those tables.

**Why not the CNINFO PDF path.** ``cninfo_disclosure`` extracts the filing body and lifts
the shareholder *count* out of it, but refuses the shareholder *tables*: in the PDF text
layer their headers split (``期末持股数 量``) and the values lose column binding, so the
table cannot be reconstructed without guessing which number belongs to which column. The
relay publishes the same table already bound to its columns, which is why this channel
exists rather than a cleverer regex.

Verified contract (probed live 2026-10-03)::

    GET https://datacenter-web.eastmoney.com/api/data/v1/get
        reportName=RPT_F10_EH_HOLDERS | RPT_F10_EH_FREEHOLDERS
        columns=ALL & pageNumber=1 & pageSize=N & source=WEB & client=WEB
        sortColumns=END_DATE,HOLDER_RANK & sortTypes=-1,1
        filter=(SECURITY_CODE="600519")                         -> latest period first
        filter=(SECURITY_CODE="600519")(END_DATE='2026-06-30')  -> that period only

    600519 2026-06-30 rank 1: HOLDER_NAME 中国贵州茅台酒厂(集团)有限责任公司,
    HOLD_NUM 681282935, HOLD_NUM_RATIO 54.5, TOTAL_SHARES_NUM 1250081601

**Units, checked rather than assumed.** ``HOLD_NUM`` is a raw share count and
``HOLD_NUM_RATIO`` is a percentage: for the sample above 681282935 / 1250081601 = 54.4999%,
which matches the published 54.5. The same arithmetic holds on 000001 (49.56),
688111 (51.38) and 300750 (22.04), so the basis is verified, not inferred from one name.
The free-float table's ``FREE_HOLDNUM_RATIO`` is a percentage too (54.499077056651),
carried to the precision the vendor publishes rather than rounded here.

**What is deliberately not claimed.** The two tables are separate reports and their rows
are not merged or deduplicated into a single ownership view, because a holder can appear
in both with a different meaning (all shares vs tradable shares only). No sum, no
concentration ratio and no float percentage is derived: those would be Mira-computed
numbers and would need a calculation-ledger row (arch doc §8), and the per-row values the
issuer disclosed are the claim this channel is able to support. An empty field stays
absent - it never becomes a zero.

Tier: the *content* is the issuer's own disclosed table (L1), but the *publisher* here is
an aggregator, so the record is L5 reported_fact with the issuer's filing named as the
upgrade path on every record - the same reasoning as ``em_insider`` and ``em_lockup``.
"""

from __future__ import annotations

import datetime as _dt
import re
import urllib.parse
from typing import Optional

from .. import config, net
from ..canonical import POSTURES, CanonicalRecord, FetchResult
from .hithink_finance import resolve_thscode

URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Referer": "https://data.eastmoney.com/",
}

#: table key -> (vendor report name, rank metric, ratio metric, endpoint template)
# The two rank metric names are deliberately distinct: a holder's rank by total holding and
# its rank among tradable shareholders are different facts about the same person, so a shared
# metric name would let one be read as the other in an evidence log.
TABLES = {
    "top10": ("RPT_F10_EH_HOLDERS",
              "holder_top10_rank", "holder_holding_ratio",
              "eastmoney://top10-holders/{symbol}"),
    "free_float": ("RPT_F10_EH_FREEHOLDERS",
                   "holder_free_float_rank", "holder_free_float_ratio",
                   "eastmoney://top10-free-float-holders/{symbol}"),
}

# The ratio basis differs between the two tables and must travel with the value: the same
# holder's share of 总股本 and share of 流通A股 are different denominators. Each entry
# carries the machine-facing field name, the Chinese label the vendor publishes, the short
# label used inside a claim sentence, and the English gloss.
RATIO_BASIS = {
    "top10": ("HOLD_NUM_RATIO", "占总股本比例", "占总股本",
              "share of total share capital"),
    "free_float": ("FREE_HOLDNUM_RATIO", "占流通股比例", "占流通股",
                   "share of tradable A-shares"),
}
UPGRADE_NOTE = ("the issuer discloses both tables in its periodic report, hosted by the "
                "exchange/CNINFO; that filing is the L1 route to the same numbers")
# A page of 10 is exactly one table; the extra rows absorb a bleed from the previous period.
PAGE_SIZE = 25
SKIPPED_FIELDS = ("HOLDER_CODE", "HOLD_NUM_ABBR", "HOLDER_STATE_NEW", "IS_HOLDORG")


def _tables(table: Optional[str]) -> list[str]:
    key = (table or "both").strip().lower()
    if key in {"both", "all", ""}:
        return ["top10", "free_float"]
    if key not in TABLES:
        raise net.FetchError(f"eastmoney_gap: unknown shareholder table {table!r}; "
                             f"expected one of {', '.join(sorted(TABLES))} or 'both'")
    return [key]


def _query(code: str, report: str, end_date: str) -> dict:
    """One page of exactly one disclosure period.

    Two steps are needed rather than one: the relay takes an ``END_DATE`` equality
    filter but cannot be asked for "the latest period", so the newest period is read
    from the default ``END_DATE`` descending order first and then pinned by equality.
    Without the pin a single page would mix two periods and ranks would repeat.
    """
    params = {
        "reportName": report, "columns": "ALL", "pageNumber": "1", "pageSize": str(PAGE_SIZE),
        "sortColumns": "END_DATE,HOLDER_RANK", "sortTypes": "-1,1",
        "source": "WEB", "client": "WEB",
        "filter": f'(SECURITY_CODE="{code}")(END_DATE=\'{end_date}\')',
    }
    return net.get_json(URL + "?" + urllib.parse.urlencode(params), headers=HEADERS,
                        retries=2, backoff=1.5)


def _latest_period(code: str, report: str) -> tuple[str, int]:
    """The newest ``END_DATE`` the relay holds for this issuer, plus its total row count."""
    params = {
        "reportName": report, "columns": "ALL", "pageNumber": "1", "pageSize": "1",
        "sortColumns": "END_DATE,HOLDER_RANK", "sortTypes": "-1,1",
        "source": "WEB", "client": "WEB", "filter": f'(SECURITY_CODE="{code}")',
    }
    payload = net.get_json(URL + "?" + urllib.parse.urlencode(params), headers=HEADERS,
                           retries=2, backoff=1.5)
    result = payload.get("result") if isinstance(payload, dict) else None
    rows = (result or {}).get("data") or []
    day = _day(rows[0].get("END_DATE")) if rows else ""
    if not day:
        raise net.FetchError(f"eastmoney_source_gap: no {report} row carries a usable "
                             f"END_DATE for {code}")
    return day, int((result or {}).get("count") or 0)


def _max_holders(max_holders: Optional[int]) -> int:
    raw = max_holders if max_holders is not None else config.get("MIRA_EM_HOLDERS_MAX")
    try:
        value = int(str(raw).strip()) if raw not in (None, "") else 10
    except ValueError:
        value = 10
    return max(1, min(10, value))


def fetch_shareholders(
    symbol: str,
    *,
    table: Optional[str] = "both",
    as_of: Optional[str] = None,
    market_scope: str = "CN",
    max_holders: Optional[int] = None,
) -> FetchResult:
    """One record per disclosed field of each holder row, for the newest period only."""
    as_of = as_of or _dt.date.today().isoformat()
    thscode = resolve_thscode(symbol)
    code, _board = thscode.split(".")
    holders = _max_holders(max_holders)

    records: list[CanonicalRecord] = []
    for key in _tables(table):
        report, rank_metric, ratio_metric, endpoint = TABLES[key]
        period, vendor_count = _latest_period(code, report)
        payload = _query(code, report, period)
        result = payload.get("result") if isinstance(payload, dict) else None
        rows = (result or {}).get("data") or []
        # The equality filter is server-side, but a row from a neighbouring period would
        # otherwise still be able to become a claim, so the period is re-checked here.
        rows = [row for row in rows if _day(row.get("END_DATE")) == period]
        if not rows:
            raise net.FetchError(
                f"eastmoney_source_gap: {thscode} has {report} history (count "
                f"{vendor_count}) but no row inside the latest period {period}")

        url = endpoint.format(symbol=thscode)
        for row in rows[:holders]:
            records.extend(_holder_records(thscode, row, key, rank_metric, ratio_metric,
                                           url, period, as_of, market_scope,
                                           POSTURES["em_shareholders"], vendor_count))
    if not records:
        raise net.FetchError(f"eastmoney_source_gap: no shareholder row reported for {thscode}")
    return FetchResult(records)


def _holder_records(thscode, row, key, rank_metric, ratio_metric, endpoint, period,
                    as_of, market_scope, posture, vendor_count) -> list[CanonicalRecord]:
    name = _clean(row.get("HOLDER_NAME")) or "未标注股东名称"
    ratio_field, ratio_label, ratio_short, ratio_gloss = RATIO_BASIS[key]
    rank = _number(row.get("HOLDER_RANK"))
    shares = _number(row.get("HOLD_NUM"))
    ratio = _number(row.get(ratio_field))
    change = _number(row.get("HOLD_NUM_CHANGE"))
    state = _clean(row.get("HOLDER_STATE")) or _clean(row.get("HOLD_CHANGE"))
    total = _number(row.get("TOTAL_SHARES_NUM")) if key == "top10" else None
    share_type = _clean(row.get("SHARES_TYPE"))

    common = {
        "table": "前十名股东" if key == "top10" else "前十名流通股东",
        "tableKey": key, "reportName": TABLES[key][0],
        "holderName": name, "holderRank": rank, "holderState": state,
        "disclosurePeriod": period, "sharesType": share_type,
        "totalSharesNum": total, "ratioField": ratio_field,
        "ratioBasis": f"{ratio_label} ({ratio_gloss})",
        "upgradePath": UPGRADE_NOTE, "vendorRowCount": vendor_count,
        "ratioSource": "vendor-published ratio, not recomputed here",
        "notDerived": ("no sum, concentration ratio or float percentage is derived from these "
                       "rows: any such figure would be a Mira calculation and need a ledger row"),
        "skippedFields": {f: row.get(f) for f in SKIPPED_FIELDS},
    }
    out: list[CanonicalRecord] = []

    def add(metric, value, unit, text, extra=None):
        provenance = dict(common)
        if extra:
            provenance.update(extra)
        out.append(CanonicalRecord(
            family="ownership_short_interest", research_object=thscode,
            market_scope=market_scope, metric=metric, value=value, unit=unit,
            period=period, period_type="point_in_time", as_of_date=as_of,
            source_date=period, posture=posture, url_or_path=endpoint, claim_text=text,
            provenance=provenance,
        ))

    if rank is not None:
        add(rank_metric, rank, "rank",
            f"{thscode} {period} {common['table']} 第 {int(rank)} 名：{name}")
    if shares is not None:
        add("holder_shares", shares, "shares",
            f"{thscode} {period} {name} 持股 {_fmt(shares)} 股"
            + (f"，{ratio_short} {_pct(ratio)}" if ratio is not None else ""))
    if ratio is not None:
        add(ratio_metric, ratio, "percent",
            f"{thscode} {period} {name} {ratio_label} {_pct(ratio)}")
    if change is not None:
        add("holder_share_change", change, "shares",
            f"{thscode} {period} {name} 较上期持股变动 {_fmt(change)} 股")
    return out


def _fmt(value: float) -> str:
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,}"


def _pct(value: float) -> str:
    """The vendor's published percentage, carried at the precision it published."""
    return f"{value:,.0f}%" if float(value).is_integer() else f"{value}%"


def _number(value) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"[,%\s]", "", str(value))
    if text in {"", "-", "--", "不变", "新进"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _day(value) -> str:
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    return match.group(1) + "-" + match.group(2) + "-" + match.group(3) if match else ""


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()
