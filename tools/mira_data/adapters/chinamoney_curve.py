"""中国国债收益率曲线 (China government bond yield curve) -> ``macro_series`` (L2, keyless).

**This closes the A-share equivalent of the US Treasury channel.** The equivalence map recorded
it as "endpoint located, contract not cracked"; the contract is now cracked, and the two
parameters that were missing are recorded below so nobody re-derives them.

Verified live 2026-10-04: the 2026-09-30 curve, 50 tenors from 0.083y (1.0150%) through 46y,
with the 10y at 1.6830%. The window carried 972 rows across several sessions.

**What was actually wrong.** Three things, and each one looked like "the source has no data":

1. **``bondType`` must be ``CYCC000``.** ``CYCC001`` — the value the port I read had labelled as
   国债 — returns an empty result, and so does every other nearby code. ``CYCC000`` is the
   government curve.
2. **``reference=1,2,3`` and ``termId`` are required.** Without them the response is
   ``records: []`` with ``data: None``, which reads as emptiness rather than as a rejected query.
3. **``pageSize`` is capped between 50 and 200.** ``pageSize=1000``/``500``/``200`` each return
   **HTTP 403**, so a caller who "asks for everything" is refused rather than truncated.
   ``pageSize=50`` is accepted and, usefully, page 1 holds one session's complete curve (50 rows
   = every tenor), with later pages stepping back a session.

The window is capped at one month: a wider range returns the vendor's own message
``只提供一个月历史数据查询``. That is surfaced as a labelled gap rather than an empty series.
"""

from __future__ import annotations

import datetime as _dt
import urllib.parse
from typing import Optional

from .. import config, net
from ..canonical import POSTURES, CanonicalRecord, FetchResult

BASE = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-currency/ClsYldCurvHis"
ENDPOINT = "chinamoney://ClsYldCurvHis/CYCC000"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Referer": "https://www.chinamoney.com.cn/chinese/bkcurvfxhis/",
}
# The only bond type verified to return the government curve.
GOVERNMENT_CURVE = "CYCC000"
# Accepted by the host; 200 and above answer HTTP 403.
SAFE_PAGE_SIZE = 50
MAX_WINDOW_DAYS = 30          # the vendor's own limit: 只提供一个月历史数据查询
DEFAULT_TENORS = ("0.083", "0.25", "0.5", "1.0", "2.0", "3.0", "5.0", "7.0", "10.0",
                  "15.0", "20.0", "30.0")

# Tenor label -> the vendor's yearTermStr. The sub-year points are the money-market end.
TENOR_LABELS = {"0.083": "1M", "0.25": "3M", "0.5": "6M", "0.75": "9M"}


def fetch_yield_curve(
    tenors: str = "",
    *,
    as_of: Optional[str] = None,
    market_scope: str = "CN",
    limit: Optional[int] = None,
) -> FetchResult:
    """The latest published China government bond yield curve, one claim per tenor.

    ``tenors`` is an optional comma-separated subset (``"1,5,10,30"`` or ``"10"``); without it
    the default set of benchmark tenors is claimed, because emitting all 50 points as separate
    claims would bury the curve's shape in noise. The full curve always travels in the series.
    """
    as_of = as_of or _dt.date.today().isoformat()
    wanted = _parse_tenors(tenors)
    end = _dt.date.fromisoformat(as_of)
    start = end - _dt.timedelta(days=MAX_WINDOW_DAYS)

    query = urllib.parse.urlencode({
        "lang": "CN", "reference": "1,2,3", "bondType": GOVERNMENT_CURVE,
        "startDate": start.isoformat(), "endDate": end.isoformat(),
        "termId": "1", "pageNum": "1", "pageSize": str(SAFE_PAGE_SIZE),
    })
    payload = net.get_json(f"{BASE}?{query}", headers=HEADERS, retries=2, backoff=1.5)
    rows = payload.get("records") or []
    meta = payload.get("data") or {}
    if not rows:
        message = meta.get("message") or meta.get("messageEn") or "no observations returned"
        raise net.FetchError(
            f"cgb_yield_source_gap: the curve endpoint returned no row ({message}). A window "
            f"wider than {MAX_WINDOW_DAYS} days is refused by the vendor, so check the range")

    # Page 1 carries one session's whole curve, so the newest date in it is the curve to read.
    sessions = sorted({str(r.get("newDateValueCN") or "") for r in rows if r.get("newDateValueCN")})
    if not sessions:
        raise net.FetchError("cgb_yield_source_gap: rows carried no session date")
    session = sessions[-1]
    curve = [(str(r.get("yearTermStr") or ""), r) for r in rows
             if str(r.get("newDateValueCN") or "") == session]
    curve = [(term, r) for term, r in curve if term]
    if not curve:
        raise net.FetchError(
            f"cgb_yield_source_gap: session {session} carried no tenor point")
    curve.sort(key=lambda pair: float(pair[0]))

    all_rows = [{"session": session, "tenor": term, "tenorLabel": _label(term),
                 "maturityYieldPercent": _num(r.get("maturityYieldStr")),
                 "currentYieldPercent": _num(r.get("currentYieldStr")),
                 "futureYieldPercent": _num(r.get("futureYieldStr"))}
                for term, r in curve]
    selected = [(term, r) for term, r in curve
                if term in (wanted or set(DEFAULT_TENORS))]
    if wanted and not selected:
        raise net.FetchError(
            f"cgb_yield_source_gap: none of the requested tenors {sorted(wanted)} exist on "
            f"{session}; the curve publishes {[t for t, _ in curve]}")

    posture = POSTURES["chinamoney_yield_curve"]
    records = []
    for term, row in selected:
        value = _num(row.get("maturityYieldStr"))
        if value is None:
            continue
        label = _label(term)
        records.append(CanonicalRecord(
            family="macro_series", research_object=f"CGB_YIELD_{label}",
            market_scope=market_scope, metric=f"cgb_yield_{label.lower()}",
            value=value, unit="percent", period=session, period_type="point_in_time",
            as_of_date=as_of, source_date=session, posture=posture, url_or_path=ENDPOINT,
            claim_text=(f"中国国债收益率曲线 {session} {label}（{term} 年）= {value}%"),
            provenance={
                "bondType": GOVERNMENT_CURVE, "session": session,
                "tenorYears": float(term), "tenorLabel": label,
                "maturityYieldPercent": value,
                "currentYieldPercent": _num(row.get("currentYieldStr")),
                "futureYieldPercent": _num(row.get("futureYieldStr")),
                "pointsOnCurve": len(curve), "pointsClaimed": len(selected),
                "windowStart": start.isoformat(), "windowEnd": end.isoformat(),
                "vendorTotalRows": meta.get("total"),
                "contractNote": ("bondType must be CYCC000 (CYCC001 and neighbours return an "
                                 "empty result); reference=1,2,3 and termId are REQUIRED or the "
                                 "response is records:[] with data:None, which reads as "
                                 "emptiness rather than a rejected query"),
                "pageSizeNote": (f"pageSize is capped between 50 and 200 - 200 and above answer "
                                 f"HTTP 403 - so {SAFE_PAGE_SIZE} is used, and page 1 happens "
                                 "to hold one session's complete curve"),
                "windowNote": (f"the vendor serves at most one month ({MAX_WINDOW_DAYS} days); "
                               "a wider range is refused with its own message"),
                "unitNote": ("yields are percent as published, and the 1M/3M/6M/9M labels map "
                             "the vendor's 0.083/0.25/0.5/0.75 year points"),
                "benchmarkNote": ("CGB yields are the sovereign discount curve used to price "
                                  "A-share risk premia and the equity risk premium in "
                                  "particular, so this is the CN counterpart of the US "
                                  "Treasury series rather than a market price"),
            },
        ))
    if not records:
        raise net.FetchError(
            f"cgb_yield_source_gap: session {session} had {len(curve)} points but none carried a "
            "parsable maturity yield")
    series = {"name": "cgb-yield-curve",
              "columns": ["session", "tenor", "tenorLabel", "maturityYieldPercent",
                          "currentYieldPercent", "futureYieldPercent"],
              "rows": all_rows}
    return FetchResult(records, series=series)


def fetch_yield_curve_family(
    tenors: str = "",
    *,
    as_of: Optional[str] = None,
    market_scope: str = "CN",
    limit: Optional[int] = None,
) -> FetchResult:
    """CLI entry point: the positional symbol IS the optional tenor list.

    The CLI always passes the symbol positionally, so forwarding it *and* setting ``tenors=``
    raises "multiple values for argument 'tenors'". Making the mapping explicit here keeps that
    call-site discipline out of the dispatcher.
    """
    return fetch_yield_curve(tenors, as_of=as_of, market_scope=market_scope, limit=limit)


def _parse_tenors(text: str) -> set[str]:
    """``"1,5,10"`` -> ``{"1.0","5.0","10.0"}``, matching the vendor's own string form."""
    if not text or not str(text).strip():
        return set()
    out = set()
    for part in str(text).replace("，", ",").split(","):
        chunk = part.strip().lower().rstrip("y")
        if not chunk:
            continue
        try:
            out.add(_canonical(float(chunk)))
        except ValueError:
            raise net.FetchError(
                f"cgb_yield_tenor_gap: {part!r} is not a tenor in years; pass years such as "
                "'1,5,10,30' (months are not accepted - use 0.083/0.25/0.5)")
    return out


def _canonical(years: float) -> str:
    """Render a tenor the way the vendor does.

    The vendor always keeps at least one decimal (``"1.0"``, ``"5.0"``) while sub-year points
    carry three (``"0.083"``). A naive ``rstrip("0")`` gives ``"5"`` for 5.0, which then fails to
    match the published tenor and would silently select nothing.
    """
    text = f"{years:.3f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


def _label(term: str) -> str:
    if term in TENOR_LABELS:
        return TENOR_LABELS[term]
    try:
        years = float(term)
    except ValueError:
        return term
    return f"{years:g}Y"


def _setting(name: str, default: int) -> int:
    try:
        return int((config.get(name) or str(default)).strip())
    except ValueError:
        return default


def _num(value):
    if value is None or value == "" or value == "---":
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None
