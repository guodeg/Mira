"""A-share stock-level valuation snapshot: PE / PB / PS / PCF for up to 100 names (L5).

The first **stock-level** channel for the ``valuation_snapshot`` family, which until now had only
the index-level channel from the CSI compiler. Read through the licensed vendor CLI, so the tier
is L5 and the claim is market pricing: these are the vendor's own ratio computations, not a
figure the issuer published.

Verified live (2026-10-03) with three codes::

    {"ok": true, "command": "valuation.snapshot",
     "data": {"item": [{"thscode": "600519.SH", "ticker": "600519", "name": "贵州茅台",
                        "pe_ttm": 19.320898, "pe_mrq": 17.671698, "pb_mrq": 6.26211,
                        "ps_ttm": 9.082149, "pcf_ttm": 13.211237}],
              "timestamp": 1791014378000, "total": 3}}

Two traps are worth keeping written down:

* the CLI rejects bare codes - thscodes carry the ``.SH`` / ``.SZ`` suffix (``resolve_thscode``
  does that conversion here);
* in PowerShell an **unquoted** comma list is parsed as an array before the CLI sees it, which
  surfaces as ``thscodes must be comma-separated A-share codes`` even though the arguments look
  right. Passing a list to ``subprocess`` (as this adapter does) is immune.

Absent or negative ratios are passed through untouched: the vendor's own boundary statement says
those fields may legitimately be empty or negative, and this substrate never zeroes a missing
value or filters an inconvenient one.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Optional

from .. import net
from ..canonical import POSTURES, CanonicalRecord, FetchResult
from .hithink_finance import _run, _timeout, resolve_thscode

MAX_CODES = 100
ENDPOINT = "hithink://valuation.snapshot/{symbol}"
FIELDS = (("pe_ttm", "pe_ttm"), ("pe_mrq", "pe_mrq"), ("pb_mrq", "pb_mrq"),
          ("ps_ttm", "ps_ttm"), ("pcf_ttm", "pcf_ttm"))
RATIO_BASIS = ("pe_ttm / ps_ttm / pcf_ttm are trailing-twelve-month; pe_mrq / pb_mrq are "
               "most-recent-quarter")
MISSING_POLICY = ("absent or negative vendor values are passed through as published, never "
                  "zeroed and never filtered")
_CN_TZ = _dt.timezone(_dt.timedelta(hours=8))


def _codes(symbol: str) -> list[str]:
    """Resolve, upper-case and de-duplicate a comma/space separated code list, order kept."""
    ordered: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[,\s]+", symbol or ""):
        part = part.strip()
        if not part:
            continue
        code = resolve_thscode(part)
        if code and code not in seen:
            seen.add(code)
            ordered.append(code)
    return ordered


def fetch_valuation_snapshot(
    symbol: str,
    *,
    as_of: Optional[str] = None,
    market_scope: str = "CN",
    timeout: Optional[float] = None,
) -> FetchResult:
    """Current valuation ratios for one or more A-share names (up to the vendor's 100-code cap)."""
    as_of = as_of or _dt.date.today().isoformat()
    codes = _codes(symbol)
    if not codes:
        raise net.FetchError("hithink_valuation_gap: no A-share thscode was given")
    if len(codes) > MAX_CODES:
        raise net.FetchError(
            f"hithink_valuation_batch_too_large: {len(codes)} codes were given; the vendor CLI "
            f"accepts at most {MAX_CODES} raw tokens per call")

    payload = _run(["valuation", "snapshot", "--thscodes", ",".join(codes), "--format", "json"],
                   timeout=timeout or _timeout())
    # ``_run`` returns the unwrapped ``data`` object (verified: its keys are item/timestamp/total),
    # but tolerate a full envelope too so a CLI change degrades to a clear gap rather than a
    # silent empty result.
    inner = payload.get("data") if isinstance(payload, dict) else None
    data = inner if isinstance(inner, dict) else (payload or {})
    items = data.get("item") or []
    if not items:
        raise net.FetchError(
            "hithink_valuation_gap: the vendor returned no valuation row for " + ",".join(codes))

    stamp = data.get("timestamp")
    vendor_day = _ms_day(stamp)
    posture = POSTURES["hithink_finance_valuation"]
    records: list[CanonicalRecord] = []
    for item in items:
        thscode = str(item.get("thscode") or "").upper()
        if not thscode:
            continue
        for field, metric in FIELDS:
            raw = item.get(field)
            if raw is None or raw == "":
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            label = str(item.get("name") or "").strip()
            records.append(CanonicalRecord(
                family="valuation_snapshot", research_object=thscode, market_scope=market_scope,
                metric=metric, value=value, unit="ratio",
                period=vendor_day or as_of, period_type="point_in_time", as_of_date=as_of,
                source_date=vendor_day or as_of, posture=posture,
                url_or_path=ENDPOINT.format(symbol=thscode),
                claim_text=(f"{thscode} {label} {metric} = {value:.4f} "
                            f"(vendor snapshot {vendor_day or as_of})"),
                provenance={
                    "vendorName": label, "vendorTicker": item.get("ticker"),
                    "vendorTimestamp": stamp, "vendorDay": vendor_day,
                    "vendorFields": {field: item.get(field) for field, _ in FIELDS},
                    "ratioBasis": RATIO_BASIS, "missingPolicy": MISSING_POLICY,
                    "batchSize": len(codes), "vendorTotal": data.get("total"),
                    "historyAvailable": False,
                    "historyNote": ("a snapshot only: the vendor exposes no history through this "
                                    "command, so no percentile or trend may be claimed from it"),
                },
            ))
    if not records:
        raise net.FetchError(
            "hithink_valuation_gap: the vendor rows carried no valuation field for "
            + ",".join(codes))
    return FetchResult(records)


def _ms_day(value) -> str:
    """Vendor epoch-milliseconds rendered as a Beijing-time day ('' when unusable)."""
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return ""
    try:
        return _dt.datetime.fromtimestamp(millis / 1000, _CN_TZ).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return ""
