"""FRED (St. Louis Fed) observations adapter -> canonical ``macro_series`` (keyed).

Official US macro and rates series at L2: this is the channel that carries Treasury
yields, the effective fed funds rate, CPI/PPI, unemployment and the rest of the
``fred_macro_series_api`` target the routing docs already name. BLS covers a subset of the
same ground keylessly, so the two are complements rather than duplicates.

**The endpoint is key-gated, and the adapter refuses to call it without one.**
``FRED_API_KEY`` comes from the gitignored ``private/mira-data.env`` (or the environment);
when it is absent the adapter raises a ``fred_key_gap`` naming the fix, exactly as the SEC
contact gate does — a silent empty read would be worse than a labelled gap.

**Why ``Accept-Encoding: identity`` on every request (measured, not superstition).**
``net``'s shared helper sends ``Accept-Encoding: gzip, deflate``. ``fred.stlouisfed.org``
tolerates that but drops the connection mid-response, which surfaces as
``incomplete or invalid response``; the same URL with ``identity`` returns the full body
(verified 2026-10-03 on ``/graph/fredgraph.csv?id=GDP``: 6,398 bytes / 319 lines with
identity, connection error with the default). So this adapter opts out of compression.

Contract notes: ``value`` is ``"."`` for a missing observation and must be **dropped, not
zeroed** — a zero is a claim the source never made. ``realtime_start``/``realtime_end`` are
captured in provenance because FRED returns the currently-known revision by default, and a
vintage is part of what a macro observation means.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional

from .. import config, net
from ..canonical import POSTURES, CanonicalRecord, FetchResult

OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
SERIES_COLUMNS = ["series_id", "date", "value", "realtime_start", "realtime_end"]
# Opt out of compression for this host; see the module docstring for the measurement.
REQUEST_HEADERS = {"Accept-Encoding": "identity"}
DEFAULT_OBSERVATIONS = 24
MISSING_VALUE = "."          # FRED's own marker for "no observation here"


def _limit(limit: Optional[int]) -> int:
    if limit is not None:
        return max(1, min(1000, int(limit)))
    try:
        return max(1, min(1000, int((config.get("MIRA_FRED_MAX_OBSERVATIONS")
                                     or str(DEFAULT_OBSERVATIONS)).strip())))
    except ValueError:
        return DEFAULT_OBSERVATIONS


def fetch_macro_series(
    series_id: str,
    *,
    as_of: Optional[str] = None,
    market_scope: str = "US",
    limit: Optional[int] = None,
    observation_start: Optional[str] = None,
    observation_end: Optional[str] = None,
) -> FetchResult:
    """Latest observation as the claim, the window as a bulk series.

    The claim is deliberately the *latest* point rather than the whole series: an
    evidence-log row is a claim, and "the series exists" is not one. The full window
    travels as ``series`` for the panel/CSV path.
    """
    as_of = as_of or _dt.date.today().isoformat()
    api_key = config.require_api_key("FRED_API_KEY", label="FRED")
    count = _limit(limit)

    params = {
        "series_id": series_id, "api_key": api_key, "file_type": "json",
        "sort_order": "desc", "limit": str(count),
    }
    if observation_start:
        params["observation_start"] = observation_start
    if observation_end:
        params["observation_end"] = observation_end
    url = OBSERVATIONS_URL + "?" + _encode(params)

    try:
        payload = net.get_json(url, headers=REQUEST_HEADERS, retries=2, backoff=1.5)
    except net.FetchError as exc:
        # A key the provider rejects must be distinguishable from a transport failure,
        # otherwise the user is told to retry instead of to fix the credential.
        if getattr(exc, "status", None) == 400:
            raise net.FetchError(
                f"fred_key_gap: FRED rejected the request for {series_id} (HTTP 400); the "
                "key in FRED_API_KEY may be invalid or not yet activated. "
                "Request a free key at https://fredaccount.stlouisfed.org/apikeys and put it "
                "in private/mira-data.env") from exc
        raise

    observations = payload.get("observations") if isinstance(payload, dict) else None
    if not observations:
        raise net.FetchError(
            f"fred_source_gap: FRED returned no observations for series {series_id} "
            "(the series id may not exist, or the window may be empty)")
    if payload.get("error_code"):
        raise net.FetchError(
            f"fred_source_gap: FRED error {payload.get('error_code')} for {series_id}: "
            f"{payload.get('error_message')}")

    rows, dropped = [], 0
    for obs in observations:
        raw_value = obs.get("value")
        if raw_value in (None, MISSING_VALUE, ""):
            # "." is FRED's "no observation"; it is not a zero and must not become one.
            dropped += 1
            continue
        rows.append({
            "series_id": series_id, "date": obs.get("date"),
            "value": raw_value, "realtime_start": obs.get("realtime_start"),
            "realtime_end": obs.get("realtime_end"),
        })
    if not rows:
        raise net.FetchError(
            f"fred_source_gap: FRED returned {len(observations)} rows for {series_id} but "
            f"every value was the missing marker {MISSING_VALUE!r}")

    latest = rows[0]                     # sort_order=desc
    record = CanonicalRecord(
        family="macro_series", research_object=series_id, market_scope=market_scope,
        metric=series_id, value=_num(latest["value"]), unit="value",
        period=latest["date"], period_type="point_in_time", as_of_date=as_of,
        source_date=latest["date"], posture=POSTURES["fred"],
        # The request URL carries the key in a query parameter, and this URL is recorded in
        # a TRACKED artifact, so it is redacted. Caught by reading an emitted evidence row
        # after the first live call: the raw URL had written the key into evidence-log.csv.
        url_or_path=net.redact_url(url),
        claim_text=f"FRED {series_id} {latest['date']} = {latest['value']}",
        provenance={
            "seriesId": series_id,
            "observationDate": latest["date"],
            "vintageStart": latest["realtime_start"], "vintageEnd": latest["realtime_end"],
            "observationsReturned": len(observations), "observationsUsable": len(rows),
            "missingDropped": dropped,
            "missingBasis": ("FRED encodes a missing observation as '.', which is dropped "
                             "rather than read as zero"),
            "vintageCaveat": ("FRED returns the currently-known revision unless a vintage is "
                              "requested, so realtime_start/end travel with the value"),
            "seriesIdNote": ("FRED series ids are provider identifiers; record the id, the "
                             "observation date and the retrieval date together"),
        },
    )
    series = {"name": f"macro_series-{series_id}", "columns": SERIES_COLUMNS, "rows": rows}
    return FetchResult([record], series=series)


def _encode(params: dict) -> str:
    import urllib.parse
    return urllib.parse.urlencode(params)


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return value
