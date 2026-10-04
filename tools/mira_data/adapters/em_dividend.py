"""A-share 分红送配 (dividends and rights) adapter -> canonical records (L5 relay).

The gap list flagged this as missing and it is a base A-share input: 股息率, 除权除息日, 分红方案.
Verified live 2026-10-04 against ``RPT_SHAREBONUS_DET`` (56,976 rows):

    华泰证券 601688  PRETAX_BONUS_RMB 1.8   IMPL_PLAN_PROFILE "10派1.80元(含税)"
    EQUITY_RECORD_DATE 2026-10-22   EX_DIVIDEND_DATE 2026-10-23   DIVIDENT_RATIO 0.010112

Three things this dataset gets wrong if read casually, each pinned below:

1. **Three different "ratio" fields, and only one is a yield.** ``DIVIDENT_RATIO`` is the
   dividend yield as a **ratio** (0.010112 = 1.01%, not 0.0101%). ``BONUS_IT_RATIO`` is 送转
   shares per 10 shares and ``IT_RATIO`` is 转增 per 10 shares — neither is a percentage, and on
   this row both are null because 华泰证券 only paid cash. Reading them as yields would invent a
   distribution that was never declared.
2. **``EX_DIVIDEND_DAYS`` is a countdown, not a date.** It read ``-18`` because the ex-date was 18
   days in the future at read time; the sign is what distinguishes a past ex-date from an
   upcoming one, so it is carried as a signed day offset rather than parsed as a date.
3. **``PRETAX_BONUS_RMB`` is per 10 shares in the plan text but per share in this field.**
   ``IMPL_PLAN_PROFILE`` says 10派1.80元 while the field is 1.8, so the field is already
   per-share. Emitting the field while quoting the plan text makes the convention auditable; using
   the plan text's number as per-share would overstate the dividend tenfold.

The two forward-return fields (``D10_CLOSE_ADJCHRATE`` / ``BD10_CLOSE_ADJCHRATE``) are the
vendor's own post-ex-date drift figures, reported rather than computed.
"""

from __future__ import annotations

import datetime as _dt
import urllib.parse
from typing import Optional

from .. import config, net
from ..canonical import POSTURES, CanonicalRecord, FetchResult

EM_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REPORT = "RPT_SHAREBONUS_DET"
ENDPOINT = "eastmoney://share-bonus"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Referer": "https://data.eastmoney.com/",
}
COLUMNS = ["code", "name", "reportDate", "planProfile", "progress", "dividendPerShare",
           "dividendYieldPercent", "bonusPer10", "transferPer10", "equityRecordDate",
           "exDividendDate", "exDividendDays", "noticeDate", "publishDate", "totalShares"]
DEFAULT_LIMIT = 200
TIER_NOTE = (
    "L5 aggregator relay of the issuer's own 分红送配 disclosure; prefer cninfo_announcements or "
    "exchange_announcements when the question is what was formally declared")


def fetch_dividends(
    code: str,
    *,
    as_of: Optional[str] = None,
    market_scope: str = "CN",
    max_items: Optional[int] = None,
) -> FetchResult:
    """Dividend and rights records for one A-share code, newest ex-date first.

    One claim per plan, keyed on the ex-dividend date when present, because that is the date the
    price actually adjusts and therefore the date a research question is anchored to.
    """
    as_of = as_of or _dt.date.today().isoformat()
    symbol = str(code or "").strip()
    if not symbol:
        raise net.FetchError(
            "dividend_source_gap: 分红送配 needs an A-share code (the report is keyed by code)")
    limit = max(1, min(500, int(max_items or _setting("MIRA_DIVIDEND_LIMIT", DEFAULT_LIMIT))))

    params = {
        "reportName": REPORT, "columns": "ALL", "pageNumber": "1", "pageSize": str(limit),
        "sortColumns": "EX_DIVIDEND_DATE,REPORT_DATE", "sortTypes": "-1,-1",
        "source": "WEB", "client": "WEB",
        "filter": f'(SECURITY_CODE="{symbol}")',
    }
    payload = net.get_json(EM_URL + "?" + urllib.parse.urlencode(params),
                           headers=HEADERS, retries=2, backoff=1.5)
    rows = ((payload.get("result") or {}).get("data") or []) if isinstance(payload, dict) else []
    if not rows:
        raise net.FetchError(
            f"dividend_source_gap: no 分红送配 record for {symbol}. A company that has never "
            "declared a distribution legitimately has none, so this is an absence of plans "
            "rather than a source failure - check the code before treating it as a gap")

    posture = POSTURES["em_dividend"]
    records, series_rows = [], []
    for row in rows:
        report_date = _day(row.get("REPORT_DATE"))
        record_date = _day(row.get("EQUITY_RECORD_DATE"))
        ex_date = _day(row.get("EX_DIVIDEND_DATE"))
        per_share = _num(row.get("PRETAX_BONUS_RMB"))
        ratio = _num(row.get("DIVIDENT_RATIO"))
        yield_pct = None if ratio is None else ratio * 100.0
        period = ex_date or report_date or record_date
        if not period:
            continue
        series_rows.append({
            "code": str(row.get("SECURITY_CODE") or "").strip(),
            "name": row.get("SECURITY_NAME_ABBR"), "reportDate": report_date,
            "planProfile": row.get("IMPL_PLAN_PROFILE"), "progress": row.get("ASSIGN_PROGRESS"),
            "dividendPerShare": per_share, "dividendYieldPercent": yield_pct,
            "bonusPer10": _num(row.get("BONUS_IT_RATIO")),
            "transferPer10": _num(row.get("IT_RATIO")),
            "equityRecordDate": record_date, "exDividendDate": ex_date,
            "exDividendDays": _num(row.get("EX_DIVIDEND_DAYS")),
            "noticeDate": _day(row.get("NOTICE_DATE")), "publishDate": _day(row.get("PUBLISH_DATE")),
            "totalShares": _num(row.get("TOTAL_SHARES")),
        })
        records.append(CanonicalRecord(
            family="issuer_disclosure", research_object=f"DIVIDEND_{symbol}",
            market_scope=market_scope, metric="dividend_per_share",
            value=per_share if per_share is not None else 0.0, unit="CNY_per_share",
            period=period, period_type="point_in_time", as_of_date=as_of,
            source_date=period, posture=posture, url_or_path=ENDPOINT,
            claim_text=(f"{row.get('SECURITY_NAME_ABBR')}（{symbol}）分红方案 "
                        f"{row.get('IMPL_PLAN_PROFILE')}，每股派息 {_fmt(per_share, 2)} 元（含税），"
                        f"股息率 {_fmt(yield_pct, 4)}%，除权除息日 {ex_date or 'n/a'}"),
            provenance={
                "code": symbol, "name": row.get("SECURITY_NAME_ABBR"),
                "planProfile": row.get("IMPL_PLAN_PROFILE"),
                "progress": row.get("ASSIGN_PROGRESS"),
                "reportDate": report_date,
                "pretaxBonusPerShareCNY": per_share,
                "dividendYieldPercent": yield_pct,
                "dividendRatioAsReturned": ratio,
                "bonusSharesPer10": _num(row.get("BONUS_IT_RATIO")),
                "transferSharesPer10": _num(row.get("IT_RATIO")),
                "equityRecordDate": record_date, "exDividendDate": ex_date,
                "exDividendDays": _num(row.get("EX_DIVIDEND_DAYS")),
                "noticeDate": _day(row.get("NOTICE_DATE")),
                "publishDate": _day(row.get("PUBLISH_DATE")),
                "totalShares": _num(row.get("TOTAL_SHARES")),
                "basicEps": _num(row.get("BASIC_EPS")),
                "bvps": _num(row.get("BVPS")),
                "perCapitalReserve": _num(row.get("PER_CAPITAL_RESERVE")),
                "perUnassignedProfit": _num(row.get("PER_UNASSIGN_PROFIT")),
                "pnpYoyPercent": _num(row.get("PNP_YOY_RATIO")),
                "postExReturnD10Percent": _num(row.get("D10_CLOSE_ADJCHRATE")),
                "preExReturnBD10Percent": _num(row.get("BD10_CLOSE_ADJCHRATE")),
                "ratioUnitNote": ("THREE different ratio fields and only one is a yield. "
                                  "DIVIDENT_RATIO is the dividend yield as a RATIO (0.010112 = "
                                  "1.01%), converted to percent here. BONUS_IT_RATIO is 送转 "
                                  "shares per 10 shares and IT_RATIO is 转增 per 10 shares - "
                                  "neither is a percentage, and both are null when only cash was "
                                  "paid, so reading them as yields would invent a share "
                                  "distribution that was never declared"),
                "perShareNote": ("PRETAX_BONUS_RMB is PER SHARE while IMPL_PLAN_PROFILE quotes "
                                 "per 10 shares (\"10派1.80元\" alongside a field of 1.8); both "
                                 "travel together so the convention is auditable, and using the "
                                 "plan text's number as per-share would overstate it tenfold"),
                "exDividendDaysNote": ("EX_DIVIDEND_DAYS is a signed day offset, not a date - a "
                                       "negative value means the ex-date is still in the future"),
                "forwardReturnNote": ("D10/BD10 are the vendor's own post- and pre-ex-date drift "
                                      "figures; they are reported, not computed here"),
                "tierBasis": TIER_NOTE,
            },
        ))
    if not records:
        raise net.FetchError(
            f"dividend_source_gap: {len(rows)} rows for {symbol} but none carried a usable date")
    series = {"name": f"dividends-{symbol}", "columns": COLUMNS, "rows": series_rows}
    return FetchResult(records, series=series)


def _setting(name: str, default: int) -> int:
    try:
        return int((config.get(name) or str(default)).strip())
    except ValueError:
        return default


def _day(value) -> str:
    text = str(value or "")
    return text[:10] if len(text) >= 10 and text[4] == "-" else ""


def _fmt(value, digits: int = 0) -> str:
    """Format a number, or say so when the vendor left it absent.

    ``n/a`` rather than 0, because "the vendor did not report this" and "this was zero" are
    different claims. Money-like fields pass ``digits`` explicitly: a per-share cash amount
    rendered at 0 digits turns 0.35 into "0" and 1.8 into "2", which is a wrong number rather
    than a rounded one.
    """
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
