"""BEA (Bureau of Economic Analysis) data adapter -> canonical ``macro_series`` (keyed).

Official US national, regional and industry accounts at L2 — GDP and its components,
personal income, industry value added. FRED carries some of the same ground; BEA is the
publishing agency for the NIPA tables, so it is the primary source for those rather than a
third-party relay.

Key-gated like FRED: ``BEA_API_KEY`` from the gitignored ``private/mira-data.env``. A
missing key raises ``bea_key_gap`` naming the fix.

**The error contract is a payload, not an HTTP status.** A rejected request still answers
HTTP 200 with ``BEAAPI.Results.Error`` (verified live 2026-10-03: an inactive key returns
``APIErrorCode`` 4 "This UserId is not active", an unknown key returns code 1 "Invalid
Request - Invalid API UserId"). Treating 200 as success would silently emit an empty
series, so the envelope is inspected before any data is read. For the same reason the
adapter refuses to fall back to BEA's shared ``DEMO`` id: it is deliberately not active,
and shipping it would mean publishing a gap as if it were a read.
"""

from __future__ import annotations

import datetime as _dt
import re
import urllib.parse
from typing import Optional

from .. import config, net
from ..canonical import POSTURES, CanonicalRecord, FetchResult

UNIT_NOTE = ("the unit is derived per row from BEA's own CL_UNIT / UNIT_MULT / METRIC_NAME "
             "rather than assumed: the same table can mix percent changes and levels, and a "
             "chained-dollar figure is not a current-dollar one")

DATA_URL = "https://apps.bea.gov/api/data/"
SERIES_COLUMNS = ["dataset", "table", "line_number", "frequency", "period", "value", "unit",
                  "line_description"]
DEFAULT_YEARS = 3
KEY_HINT = ("Request a free key at https://apps.bea.gov/API/signup/ and put it in "
            "private/mira-data.env as BEA_API_KEY")


def _unit(item: dict) -> str:
    """A uniform token for what the number means, from BEA's own ``CL_UNIT``/``UNIT_MULT``.

    BEA reports the unit per row, and the same table can mix kinds, so this is derived
    rather than assumed. Measured on live tables: NIPA T10101 gives ``Percent change,
    annual rate`` with ``UNIT_MULT`` 0, T10105 gives ``Level`` with ``UNIT_MULT`` 6 (so the
    figure is in millions — 31,906,274 is $31.9tn), and T10106 gives ``Level`` with
    ``Chained Dollars``, where a bare ``millions`` would imply a current-dollar basis the
    number does not have.
    """
    label = str(item.get("CL_UNIT") or "").strip()
    metric = str(item.get("METRIC_NAME") or "").strip().lower()
    mult = str(item.get("UNIT_MULT") or "").strip()
    lowered = label.lower()
    if "percent" in lowered:
        return "percent_annual_rate" if "annual" in lowered else "percent_change"
    scale = {"0": "", "3": "thousands", "6": "millions", "9": "billions"}.get(mult, "")
    if "chained" in metric:
        return f"{scale}_chained_dollars" if scale else "chained_dollars"
    if "index" in metric:
        return "index"
    if lowered in {"level", "levels"}:
        return f"{scale}_usd" if scale else "usd"
    if not label and not metric:
        return "value"          # BEA omitted both; do not invent a basis
    basis = metric or label
    # Only prefix the scale when the label does not already state its own magnitude,
    # otherwise `Millions of dollars` with UNIT_MULT=6 becomes `millions_millions_of_dollars`.
    scale_words = ("thousand", "million", "billion", "trillion")
    if scale and any(word in basis.lower() for word in scale_words):
        scale = ""
    # Compose, then normalise. Joining with an explicit separator and stripping only a
    # LEADING one avoids `str.strip("_")`, which removes from both ends and silently
    # mangles single-word labels (measured twice: "index" -> "nde", then -> "ndex").
    raw = f"{scale}_{basis}" if scale else basis
    token = re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", raw.lower())).lstrip("_")
    return token or "value"


def _years(year: Optional[str]) -> str:
    if year:
        return str(year)
    current = _dt.date.today().year
    return ",".join(str(y) for y in range(current - DEFAULT_YEARS + 1, current + 1))


def fetch_macro_dataset(
    table: str,
    *,
    dataset: str = "NIPA",
    frequency: str = "Q",
    year: Optional[str] = None,
    as_of: Optional[str] = None,
    market_scope: str = "US",
) -> FetchResult:
    """Latest period of one BEA table as the claim, the returned window as a series.

    ``table`` is the BEA table name (e.g. ``T10101`` for the NIPA GDP table); ``dataset``
    selects the product (NIPA, NIUnderlyingDetail, MNE, GDPbyIndustry, Regional, ...).
    Both are provider identifiers and are recorded verbatim in provenance, because a BEA
    number is only meaningful with its table identity attached.
    """
    as_of = as_of or _dt.date.today().isoformat()
    api_key = config.require_api_key("BEA_API_KEY", label="BEA")
    window = _years(year)

    params = {
        "UserID": api_key, "method": "GetData", "datasetname": dataset,
        "TableName": table, "Frequency": frequency, "Year": window,
        "ResultFormat": "JSON",
    }
    url = DATA_URL + "?" + urllib.parse.urlencode(params)
    payload = net.get_json(url, retries=2, backoff=1.5)

    envelope = payload.get("BEAAPI") if isinstance(payload, dict) else None
    if not isinstance(envelope, dict):
        raise net.FetchError(
            f"bea_source_gap: unexpected BEA envelope for {dataset}/{table} "
            f"(no BEAAPI key in the response)")
    # Checked BEFORE reading data: BEA answers HTTP 200 with an Error payload on rejection.
    error = (envelope.get("Results") or {}).get("Error")
    if error:
        code = str(error.get("APIErrorCode") or "?")
        description = error.get("APIErrorDescription") or "no description"
        if code in {"1", "4"} or "userid" in description.lower():
            raise net.FetchError(
                f"bea_key_gap: BEA rejected UserID for {dataset}/{table} "
                f"(code {code}: {description}). {KEY_HINT}")
        raise net.FetchError(
            f"bea_source_gap: BEA error {code} for {dataset}/{table}: {description}")

    data = (envelope.get("Results") or {}).get("Data") or []
    if not data:
        raise net.FetchError(
            f"bea_source_gap: BEA returned no rows for {dataset}/{table} "
            f"(frequency {frequency}, years {window})")

    rows = []
    for item in data:
        period = str(item.get("TimePeriod") or "").strip()
        value = str(item.get("DataValue") or "").strip()
        if not period or not value or value in {"(D)", "(NA)", "(L)", "(NM)", "..."}:
            # BEA marks suppressed/not-applicable cells in parentheses; they are not zeros.
            continue
        rows.append({
            "dataset": dataset, "table": table,
            "line_number": str(item.get("LineNumber") or "").strip(),
            "frequency": frequency, "period": period, "value": value,
            "unit": _unit(item),
            "line_description": str(item.get("LineDescription") or "").strip(),
        })
    if not rows:
        raise net.FetchError(
            f"bea_source_gap: BEA returned {len(data)} rows for {dataset}/{table} but every "
            "one was suppressed or empty, so there is no observation to claim")

    latest = rows[0]
    line = latest["line_number"] or "?"
    record = CanonicalRecord(
        family="macro_series", research_object=f"{dataset}.{table}",
        market_scope=market_scope,
        # The LINE is part of the identity, not decoration: a BEA table returns many line
        # items (T10105 alone carries GDP, personal consumption, goods, services, ...), so
        # a metric named only after the table cannot say which quantity it holds. This was
        # found by reading live output: `NIPA.T10101 0.1` was indistinguishable from a
        # level series until the line was named.
        metric=f"{dataset}.{table}.line{line}", value=_num(latest["value"]),
        unit=latest["unit"], period=_period(latest["period"]),
        period_type="calendar_period",
        as_of_date=as_of, source_date=as_of, posture=POSTURES["bea"],
        # UserID is a credential in the query string, and this URL lands in tracked
        # artifacts, so it is redacted rather than recorded verbatim.
        url_or_path=net.redact_url(url),
        claim_text=(f"BEA {dataset} {table} line {line} "
                    f"{latest['line_description'] or '(unnamed line)'} "
                    f"{latest['period']} = {latest['value']} {latest['unit']}"),
        provenance={
            "dataset": dataset, "table": table, "frequency": frequency,
            "lineNumber": line, "lineDescription": latest["line_description"],
            "unitBasis": latest["unit"], "unitNote": UNIT_NOTE,
            "periodRaw": latest["period"],
            "yearsRequested": window, "rowsReturned": len(data), "rowsUsable": len(rows),
            "suppressedDropped": len(data) - len(rows),
            "suppressedBasis": ("BEA encodes suppressed or not-applicable cells in "
                                "parentheses ((D)/(NA)/(L)/(NM)); they are dropped rather "
                                "than read as zero"),
            "tableIdentity": ("a BEA value is only meaningful with its dataset, table and "
                              "line attached, so all three travel on every record"),
            "licenseNote": ("BEA data is public domain, but the key identifies the caller; "
                            "keep the key in private/mira-data.env only"),
        },
    )
    series = {"name": f"macro_series-{dataset}-{table}", "columns": SERIES_COLUMNS, "rows": rows}
    return FetchResult([record], series=series)


def _period(raw: str) -> str:
    """``2026Q2`` -> ``2026-Q2``; annual ``2026`` stays ``2026``."""
    text = (raw or "").strip()
    if len(text) == 6 and text[4] in "Qq":
        return f"{text[:4]}-{text[4:].upper()}"
    if len(text) == 6 and text[4] in "Mm":
        return f"{text[:4]}-{text[4:].upper()}"
    return text


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return value
