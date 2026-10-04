"""A-share 股权质押 (share pledges) adapter -> canonical records (L5 relay of CSDC data).

The last of the three A-share holes the equivalence map identified. Three views, all verified live
2026-10-04:

- ``market`` — ``RPT_CSDC_STATISTICS``: whole-market pledged shares, pledged market value, the
  number of pledging companies, the CSI 300 level alongside, and daily deal counts. 2026-09-30
  read 28,064,095.49 万股 pledged across 2,211 companies.
- ``stock`` — ``RPT_CSDC_LIST``: per-stock 质押比例 with the repurchase balance split into
  unlimited/limited portions, deal count, pledge market cap and industry. 1,639,892 rows.
- ``institution`` — ``RPT_GDZY_ZYJG_SUM``: per-pledgee (券商) summary with the three warning
  states (``WARNING_STATE_1/2/3``), deal and pledge counts. 83 institutions.

**The freshness trap, which is the reason this adapter reports dates so insistently.** A stock
that no longer pledges simply stops appearing, so its newest row is its *last* observation rather
than a current one: 万科A's newest row is **2024-04-30** while 600519/000001/002415/300750 are all
current to 2026-09-30. Treating a stale row as current would report a two-year-old pledge ratio as
today's, and treating the absence as zero would claim a company has no pledges when the register
simply has nothing recent. The record therefore carries ``lastObservedDate`` and an explicit
``stalenessDays``, and the claim text names the date it belongs to.

**Tier is L5, and the provenance says why it is close to L2.** The underlying data is 中国结算's
(CSDC, the depository) official weekly aggregate; this is a relay of it, so the depository's own
publication remains controlling.

Units are 万股 for share counts and 万元 for money across all three reports, consistent with the
other A-share relays — and NOT the raw shares/yuan that the block-trade detail report uses.
"""

from __future__ import annotations

import datetime as _dt
import urllib.parse
from typing import Optional

from .. import config, net
from ..canonical import POSTURES, CanonicalRecord, FetchResult

EM_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
MARKET_REPORT = "RPT_CSDC_STATISTICS"
STOCK_REPORT = "RPT_CSDC_LIST"
INSTITUTION_REPORT = "RPT_GDZY_ZYJG_SUM"
ENDPOINT = "eastmoney://share-pledge"     # literal: the view travels in each record's provenance
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Referer": "https://data.eastmoney.com/gpzy/pledgeRatio.aspx",
}
VIEWS = ("market", "stock", "institution")
MARKET_COLUMNS = ["tradeDate", "pledgedSharesWan", "pledgedMarketValueWan", "csi300",
                  "pledgingCompanies", "dailyDeals"]
STOCK_COLUMNS = ["tradeDate", "code", "name", "pledgeRatioPercent",
                 "repurchaseBalanceWanShares", "dealNum", "unlimitedBalanceWanShares",
                 "limitedBalanceWanShares", "pledgeMarketCapWan", "industry"]
INSTITUTION_COLUMNS = ["name", "orgType", "orgNum", "dealNum", "pledgeWan",
                       "warningState1", "warningState2", "warningState3"]
DEFAULT_LIMIT = 100
TIER_NOTE = (
    "L5 relay of 中国结算 (CSDC) share-pledge data; the depository's own publication remains the "
    "controlling source")


def fetch_share_pledge(
    symbol: str = "",
    *,
    view: str = "stock",
    as_of: Optional[str] = None,
    market_scope: str = "CN",
    max_items: Optional[int] = None,
) -> FetchResult:
    """Share-pledge records for one stock, one institution list, or the whole market.

    ``view="stock"`` needs a code; ``view="market"`` and ``view="institution"`` are market-wide and
    take no symbol.
    """
    as_of = as_of or _dt.date.today().isoformat()
    which = (view or "stock").strip().lower()
    if which not in VIEWS:
        raise net.FetchError(
            f"pledge_view_gap: {view!r} is not a view; this adapter reads "
            f"{'/'.join(VIEWS)} (market-wide, per stock, or per pledgee institution)")
    limit = max(1, min(500, int(max_items or _setting("MIRA_PLEDGE_LIMIT", DEFAULT_LIMIT))))

    if which == "stock":
        code = str(symbol or "").strip()
        if not code:
            raise net.FetchError(
                "pledge_source_gap: the per-stock view needs an A-share code (use view=market or "
                "view=institution for the market-wide reports)")
        rows = _query(STOCK_REPORT, "TRADE_DATE", limit, f'(SECURITY_CODE="{code}")')
        if not rows:
            raise net.FetchError(
                f"pledge_source_gap: no pledge record for {code}. A company absent from the "
                "register is not one with zero pledges - it means CSDC lists no outstanding "
                "pledge for it, so do not read the absence as a zero ratio")
        return _stock_view(rows, code, as_of, market_scope, limit)

    if which == "market":
        rows = _query(MARKET_REPORT, "TRADE_DATE", limit, None)
        if not rows:
            raise net.FetchError("pledge_source_gap: the market report returned no row")
        return _market_view(rows, as_of, market_scope, limit)

    rows = _query(INSTITUTION_REPORT, "ORG_NUM", limit, None)
    if not rows:
        raise net.FetchError("pledge_source_gap: the institution report returned no row")
    return _institution_view(rows, as_of, market_scope, limit)


def _stock_view(rows, code, as_of, market_scope, limit) -> FetchResult:
    posture = POSTURES["em_share_pledge"]
    newest = _day(rows[0].get("TRADE_DATE"))
    fresh = _staleness(newest, as_of)
    records, series_rows = [], []
    for row in rows[:limit]:
        day = _day(row.get("TRADE_DATE"))
        ratio = _num(row.get("PLEDGE_RATIO"))
        series_rows.append({
            "tradeDate": day, "code": str(row.get("SECURITY_CODE") or "").strip(),
            "name": row.get("SECURITY_NAME_ABBR"), "pledgeRatioPercent": ratio,
            "repurchaseBalanceWanShares": _num(row.get("REPURCHASE_BALANCE")),
            "dealNum": _num(row.get("PLEDGE_DEAL_NUM")),
            "unlimitedBalanceWanShares": _num(row.get("REPURCHASE_UNLIMITED_BALANCE")),
            "limitedBalanceWanShares": _num(row.get("REPURCHASE_LIMITED_BALANCE")),
            "pledgeMarketCapWan": _num(row.get("PLEDGE_MARKET_CAP")),
            "industry": row.get("INDUSTRY"),
        })
    records.append(CanonicalRecord(
        family="ownership_short_interest", research_object=f"PLEDGE_{code}",
        market_scope=market_scope, metric="pledge_ratio", value=_num(rows[0].get("PLEDGE_RATIO")) or 0.0,
        unit="percent", period=newest, period_type="point_in_time", as_of_date=as_of,
        source_date=newest, posture=posture, url_or_path=ENDPOINT,
        claim_text=(f"{rows[0].get('SECURITY_NAME_ABBR')}（{code}）股权质押比例 "
                    f"{_fmt(_num(rows[0].get('PLEDGE_RATIO')), 2)}%（截至 {newest}）"),
        provenance={
            "view": "stock", "code": code, "name": rows[0].get("SECURITY_NAME_ABBR"),
            "lastObservedDate": newest, "stalenessDays": fresh,
            "pledgeRatioPercent": _num(rows[0].get("PLEDGE_RATIO")),
            "repurchaseBalanceWanShares": _num(rows[0].get("REPURCHASE_BALANCE")),
            "unlimitedBalanceWanShares": _num(rows[0].get("REPURCHASE_UNLIMITED_BALANCE")),
            "limitedBalanceWanShares": _num(rows[0].get("REPURCHASE_LIMITED_BALANCE")),
            "dealNum": _num(rows[0].get("PLEDGE_DEAL_NUM")),
            "pledgeMarketCapWan": _num(rows[0].get("PLEDGE_MARKET_CAP")),
            "industry": rows[0].get("INDUSTRY"),
            "observationsReturned": len(rows),
            "stalenessNote": ("a company that stops pledging simply stops appearing, so this is "
                              "its LAST observed ratio rather than a current one - check "
                              "stalenessDays before treating it as today's position"),
            "absenceNote": ("a company absent from this report is not one with zero pledges; it "
                            "means CSDC lists no outstanding pledge for it"),
            "unitNote": ("the BALANCE fields are pledged SHARES in 万股 while PLEDGE_MARKET_CAP "
                         "is money in 万元 - the two are different quantities, not two "
                         "currencies worth of the same thing"),
            "unitBasis": ("established by arithmetic: PLEDGE_MARKET_CAP / REPURCHASE_BALANCE "
                          "returns a per-SHARE price (000981: 2,500,317.43 / 222,448.17 = "
                          "11.24 yuan, and 11.50 / 10.44 on adjacent dates), which only holds "
                          "if the balance is SHARES. Reading it as 万元 would make a "
                          "2.2-billion-share pledge look like 2.2 million yuan of money, and "
                          "the field name says 'balance' either way"),
            "tierBasis": TIER_NOTE,
        },
    ))
    series = {"name": f"share-pledge-{code}", "columns": STOCK_COLUMNS, "rows": series_rows}
    return FetchResult(records, series=series)


def _market_view(rows, as_of, market_scope, limit) -> FetchResult:
    posture = POSTURES["em_share_pledge"]
    newest = _day(rows[0].get("TRADE_DATE"))
    series_rows = [{
        "tradeDate": _day(r.get("TRADE_DATE")),
        "pledgedSharesWan": _num(r.get("TOTAL_PLEDGED_SHARES")),
        "pledgedMarketValueWan": _num(r.get("PLEDGE_MARKET_VALUE")),
        "csi300": _num(r.get("CSI_300_INDEX")),
        "pledgingCompanies": _num(r.get("PLEDGE_CO_NUM")),
        "dailyDeals": _num(r.get("DAILY_STATISTICS")),
    } for r in rows[:limit]]
    head = series_rows[0]
    records = [CanonicalRecord(
        family="ownership_short_interest", research_object="PLEDGE_MARKET",
        market_scope=market_scope, metric="market_pledged_shares",
        value=head["pledgedSharesWan"] or 0.0, unit="10k_shares", period=newest,
        period_type="point_in_time", as_of_date=as_of, source_date=newest, posture=posture,
        url_or_path=ENDPOINT,
        claim_text=(f"A股全市场质押股份 {newest} = {_fmt(head['pledgedSharesWan'])} 万股，"
                    f"质押市值 {_fmt(head['pledgedMarketValueWan'])} 万元，"
                    f"质押公司 {_fmt(head['pledgingCompanies'])} 家"),
        provenance={
            "view": "market", "tradeDate": newest,
            "pledgedSharesWan": head["pledgedSharesWan"],
            "pledgedMarketValueWan": head["pledgedMarketValueWan"],
            "csi300": head["csi300"], "pledgingCompanies": head["pledgingCompanies"],
            "dailyDeals": head["dailyDeals"],
            "observationsReturned": len(rows),
            "unitNote": ("万股 for share counts and 万元 for money, as in the other A-share "
                         "relays; the series carries the history so a trend is readable"),
            "tierBasis": TIER_NOTE,
        },
    )]
    series = {"name": "share-pledge-market", "columns": MARKET_COLUMNS, "rows": series_rows}
    return FetchResult(records, series=series)


def _institution_view(rows, as_of, market_scope, limit) -> FetchResult:
    posture = POSTURES["em_share_pledge"]
    records, series_rows = [], []
    for row in rows[:limit]:
        name = str(row.get("SECURITY_NAME_ABBR") or "").strip()
        if not name:
            continue
        series_rows.append({
            "name": name, "orgType": row.get("PFORG_TYPE"), "orgNum": _num(row.get("ORG_NUM")),
            "dealNum": _num(row.get("PLEDGE_DEAL_NUM")), "pledgeWan": _num(row.get("PLEDGE_NUM")),
            "warningState1": _num(row.get("WARNING_STATE_1")),
            "warningState2": _num(row.get("WARNING_STATE_2")),
            "warningState3": _num(row.get("WARNING_STATE_3")),
        })
        records.append(CanonicalRecord(
            family="ownership_short_interest", research_object=f"PLEDGEE_{name}",
            market_scope=market_scope, metric="pledgee_pledge_shares",
            value=_num(row.get("PLEDGE_NUM")) or 0.0, unit="10k_shares", period=as_of,
            period_type="point_in_time", as_of_date=as_of, source_date=as_of, posture=posture,
            url_or_path=ENDPOINT,
            claim_text=(f"质押机构「{name}」质押股数 {_fmt(_num(row.get('PLEDGE_NUM')))} 万股，"
                        f"{_fmt(_num(row.get('ORG_NUM')))} 家 / "
                        f"{_fmt(_num(row.get('PLEDGE_DEAL_NUM')))} 笔"),
            provenance={
                "view": "institution", "name": name, "orgType": row.get("PFORG_TYPE"),
                "orgNum": _num(row.get("ORG_NUM")), "dealNum": _num(row.get("PLEDGE_DEAL_NUM")),
                "pledgeWan": _num(row.get("PLEDGE_NUM")),
                "warningState1": _num(row.get("WARNING_STATE_1")),
                "warningState2": _num(row.get("WARNING_STATE_2")),
                "warningState3": _num(row.get("WARNING_STATE_3")),
                "warningState1Rate": _num(row.get("WARNING_STATE_1_RATE")),
                "warningNote": ("WARNING_STATE_1/2/3 are the vendor's three pledge-warning "
                                "buckets; they are reported as counts and the bucket definitions "
                                "are NOT asserted here, so treat the split as the vendor's own "
                                "classification rather than a Mira-derived one"),
                "asOfNote": ("this report carries no observation date of its own, so as_of is "
                             "recorded - it is a current snapshot rather than a dated series"),
                "unitNote": "万股, as in the other A-share relays",
                "tierBasis": TIER_NOTE,
            },
        ))
    if not records:
        raise net.FetchError("pledge_source_gap: the institution rows carried no usable name")
    series = {"name": "share-pledge-institutions", "columns": INSTITUTION_COLUMNS,
              "rows": series_rows}
    return FetchResult(records, series=series)


def _query(report: str, sort_column: str, limit: int, extra_filter: Optional[str]) -> list[dict]:
    params = {
        "reportName": report, "columns": "ALL", "pageNumber": "1", "pageSize": str(limit),
        "sortColumns": sort_column, "sortTypes": "-1", "source": "WEB", "client": "WEB",
    }
    if extra_filter:
        params["filter"] = extra_filter
    payload = net.get_json(EM_URL + "?" + urllib.parse.urlencode(params),
                           headers=HEADERS, retries=2, backoff=1.5)
    return ((payload.get("result") or {}).get("data") or []) if isinstance(payload, dict) else []


def _staleness(day: str, as_of: str):
    if not day:
        return None
    try:
        return (_dt.date.fromisoformat(as_of) - _dt.date.fromisoformat(day)).days
    except ValueError:
        return None


def _setting(name: str, default: int) -> int:
    try:
        return int((config.get(name) or str(default)).strip())
    except ValueError:
        return default


def _day(value) -> str:
    text = str(value or "")
    return text[:10] if len(text) >= 10 and text[4] == "-" else ""


def _fmt(value, digits: int = 0) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.{digits}f}" if digits else f"{value:,.0f}"


def _num(value):
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None
