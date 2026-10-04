#!/usr/bin/env python3
"""Offline tests for the keyed US macro adapters (FRED and BEA).

Both endpoints were probed live on 2026-10-03; these tests pin the behaviour that the
probes exposed, because each one is a way to publish a wrong or empty answer silently:

1. **A missing key must be a labelled gap, not a silent empty read.** The gap is raised
   from ``config`` (which cannot import ``net``) yet has to be catchable as
   ``net.FetchError`` by every adapter's existing handler. Getting this wrong is easy: the
   MRO looked right while ``except FetchError`` still missed it, because the *parent* was
   being raised. `test_missing_key_is_catchable_as_fetch_error` is that regression.
2. **FRED's ``.`` is "no observation", never 0.** A zero is a claim the source never made.
3. **BEA reports rejection inside an HTTP 200 payload.** Treating 200 as success emits an
   empty series from a request that was actually refused.
4. **FRED needs ``Accept-Encoding: identity``.** The shared helper's ``gzip, deflate``
   makes the host drop the connection (verified: 6,398 bytes with identity, connection
   error without), so the adapter must opt out.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import config, net
from tools.mira_data.adapters import bea, fred


FRED_PAYLOAD = {
    "realtime_start": "2026-10-03", "realtime_end": "2026-10-03",
    "observations": [
        {"realtime_start": "2026-10-03", "realtime_end": "2026-10-03",
         "date": "2026-04-01", "value": "31433.326"},
        {"realtime_start": "2026-10-03", "realtime_end": "2026-10-03",
         "date": "2026-01-01", "value": "31225.336"},
        {"realtime_start": "2026-10-03", "realtime_end": "2026-10-03",
         "date": "2025-10-01", "value": "."},
    ],
}

BEA_PAYLOAD = {"BEAAPI": {
    "Request": {"RequestParam": [{"ParameterName": "METHOD", "ParameterValue": "GetData"}]},
    "Results": {"Data": [
        {"TimePeriod": "2026Q2", "DataValue": "32,563,030", "LineNumber": "1",
         "LineDescription": "Gross domestic product", "CL_UNIT": "Level", "UNIT_MULT": "6"},
        {"TimePeriod": "2026Q1", "DataValue": "31,906,274", "LineNumber": "1",
         "LineDescription": "Gross domestic product", "CL_UNIT": "Level", "UNIT_MULT": "6"},
        {"TimePeriod": "2026Q1", "DataValue": "21,525,983", "LineNumber": "2",
         "LineDescription": "Personal consumption expenditures", "CL_UNIT": "Level",
         "UNIT_MULT": "6"},
        {"TimePeriod": "2025Q4", "DataValue": "(D)", "LineNumber": "1",
         "LineDescription": "Gross domestic product", "CL_UNIT": "Level", "UNIT_MULT": "6"},
    ]},
}}

BEA_INACTIVE_KEY = {"BEAAPI": {"Results": {"Error": {
    "APIErrorCode": "4", "APIErrorDescription": "This UserId is not active. Please activate it and try again."}}}}
BEA_BAD_KEY = {"BEAAPI": {"Results": {"Error": {
    "APIErrorCode": "1", "APIErrorDescription": "Invalid Request - Invalid API UserId."}}}}


def _with_key(name, value="test-key"):
    return mock.patch.object(config, "api_key", side_effect=lambda n: value if n == name else None)


def test_missing_key_is_catchable_as_fetch_error() -> None:
    """The regression: a config gap raised as the PARENT is not caught as FetchError."""
    with mock.patch.object(config, "api_key", return_value=None):
        try:
            config.require_api_key("FRED_API_KEY", label="FRED")
        except net.FetchError as exc:
            assert "FRED_key_gap" in str(exc) and "FRED_API_KEY" in str(exc)
        else:
            raise AssertionError("a missing key must raise, not return")

        for module, call in ((fred, lambda: fred.fetch_macro_series("GDP")),
                             (bea, lambda: bea.fetch_macro_dataset("T10101"))):
            try:
                call()
            except net.FetchError as exc:
                assert "key_gap" in str(exc), str(exc)
                assert "mira-data.env" in str(exc), "the gap must name where to put the key"
            else:
                raise AssertionError(f"{module.__name__} must refuse without a key")
    print("ok a missing key raises a config gap that adapters catch as net.FetchError")


def test_fred_missing_marker_is_dropped_not_zeroed() -> None:
    with _with_key("FRED_API_KEY"), \
            mock.patch.object(fred.net, "get_json", return_value=FRED_PAYLOAD):
        result = fred.fetch_macro_series("GDP", as_of="2026-10-03")
    rec = result.records[0]
    # Latest usable observation wins; the "." row is not the latest anyway, so also assert
    # it never reached the series as a zero.
    assert rec.value == 31433.326 and rec.period == "2026-04-01"
    values = [row["value"] for row in result.series["rows"]]
    assert "." not in values and 0 not in values and "0" not in values
    assert rec.provenance["missingDropped"] == 1
    assert "not read as zero" in rec.provenance["missingBasis"] or \
        "rather than read as zero" in rec.provenance["missingBasis"]
    assert rec.provenance["vintageStart"] == "2026-10-03"

    all_missing = {"observations": [{"date": "2026-04-01", "value": "."}]}
    with _with_key("FRED_API_KEY"), \
            mock.patch.object(fred.net, "get_json", return_value=all_missing):
        try:
            fred.fetch_macro_series("GDP", as_of="2026-10-03")
        except net.FetchError as exc:
            assert "fred_source_gap" in str(exc)
        else:
            raise AssertionError("an all-missing series must be a gap, not an empty claim")
    print("ok FRED's '.' is dropped as missing and an all-missing window is a gap")


def test_fred_opts_out_of_compression() -> None:
    with _with_key("FRED_API_KEY"), \
            mock.patch.object(fred.net, "get_json", return_value=FRED_PAYLOAD) as getter:
        fred.fetch_macro_series("GDP", as_of="2026-10-03")
    headers = getter.call_args[1].get("headers") or {}
    assert headers.get("Accept-Encoding") == "identity", headers
    url = getter.call_args[0][0]
    assert "series_id=GDP" in url and "api_key=test-key" in url and "file_type=json" in url
    print("ok FRED requests identity encoding (the gzip variant drops the connection)")


def test_fred_rejected_key_is_a_key_gap_not_a_retry() -> None:
    with _with_key("FRED_API_KEY"), \
            mock.patch.object(fred.net, "get_json",
                              side_effect=net.FetchError("HTTP 400 for url", status=400, url="u")):
        try:
            fred.fetch_macro_series("GDP", as_of="2026-10-03")
        except net.FetchError as exc:
            assert "fred_key_gap" in str(exc), str(exc)
            assert "HTTP 400" in str(exc)
        else:
            raise AssertionError("a rejected key must be labelled as a key problem")
    print("ok an HTTP 400 from FRED is reported as a key gap, not a transient failure")


def test_bea_error_inside_http_200_is_not_success() -> None:
    for payload, expect_key in ((BEA_INACTIVE_KEY, True), (BEA_BAD_KEY, True)):
        with _with_key("BEA_API_KEY"), \
                mock.patch.object(bea.net, "get_json", return_value=payload):
            try:
                bea.fetch_macro_dataset("T10101", as_of="2026-10-03")
            except net.FetchError as exc:
                message = str(exc)
                assert ("bea_key_gap" in message) == expect_key, message
                assert "BEA_API_KEY" in message
            else:
                raise AssertionError("a 200 with an Error payload must not be read as data")

    # A non-credential BEA error is a source gap, not a key gap.
    other = {"BEAAPI": {"Results": {"Error": {"APIErrorCode": "9",
                                              "APIErrorDescription": "Unsupported frequency"}}}}
    with _with_key("BEA_API_KEY"), mock.patch.object(bea.net, "get_json", return_value=other):
        try:
            bea.fetch_macro_dataset("T10101", as_of="2026-10-03")
        except net.FetchError as exc:
            assert "bea_source_gap" in str(exc) and "key_gap" not in str(exc)
        else:
            raise AssertionError("a non-credential error must be a source gap")
    print("ok BEA rejection inside HTTP 200 is caught and split into key vs source gap")


def test_bea_drops_suppressed_cells_and_normalises_periods() -> None:
    with _with_key("BEA_API_KEY"), \
            mock.patch.object(bea.net, "get_json", return_value=BEA_PAYLOAD):
        result = bea.fetch_macro_dataset("T10105", year="2026", as_of="2026-10-03")
    rec = result.records[0]
    assert rec.period == "2026-Q2", rec.period
    assert rec.provenance["periodRaw"] == "2026Q2"
    assert rec.provenance["suppressedDropped"] == 1
    assert all("(D)" not in row["value"] for row in result.series["rows"])
    assert rec.provenance["tableIdentity"]

    only_suppressed = {"BEAAPI": {"Results": {"Data": [
        {"TimePeriod": "2026Q2", "DataValue": "(D)", "LineNumber": "1"}]}}}
    with _with_key("BEA_API_KEY"), \
            mock.patch.object(bea.net, "get_json", return_value=only_suppressed):
        try:
            bea.fetch_macro_dataset("T10105", year="2026", as_of="2026-10-03")
        except net.FetchError as exc:
            assert "bea_source_gap" in str(exc) and "suppressed" in str(exc)
        else:
            raise AssertionError("an all-suppressed table must be a gap")
    print("ok BEA drops suppressed cells and normalises 2026Q2 to 2026-Q2")


def test_bea_names_the_line_and_derives_the_unit() -> None:
    """A table holds many line items, so neither the metric nor the unit can be assumed.

    Found by reading live output: `NIPA.T10101 0.1` was a percent change while
    `NIPA.T10105 31,906,274` was a level in millions, and nothing in the claim said which
    line or which basis it was.
    """
    with _with_key("BEA_API_KEY"), \
            mock.patch.object(bea.net, "get_json", return_value=BEA_PAYLOAD):
        rec = bea.fetch_macro_dataset("T10105", year="2026", as_of="2026-10-03").records[0]
    # Line 1 is GDP; line 2 is personal consumption. They must not share a metric.
    assert rec.metric == "NIPA.T10105.line1", rec.metric
    assert "Gross domestic product" in rec.claim_text
    assert rec.provenance["lineNumber"] == "1"
    # Units come from CL_UNIT / UNIT_MULT, not a guessed "value".
    assert rec.unit == "millions_usd", rec.unit
    assert rec.provenance["unitBasis"] == "millions_usd"
    assert "CL_UNIT" in rec.provenance["unitNote"] or "CL_UNIT" in bea.UNIT_NOTE

    # The same table shape with a percent-change basis and a chained-dollar basis.
    def one(**fields):
        row = {"TimePeriod": "2026Q1", "DataValue": "2.5", "LineNumber": "1",
               "LineDescription": "Gross domestic product"}
        row.update(fields)
        return {"BEAAPI": {"Results": {"Data": [row]}}}

    cases = [
        ({"CL_UNIT": "Percent change, annual rate", "UNIT_MULT": "0"}, "percent_annual_rate"),
        ({"CL_UNIT": "Level", "UNIT_MULT": "6", "METRIC_NAME": "Chained Dollars"},
         "millions_chained_dollars"),
        ({"CL_UNIT": "Level", "UNIT_MULT": "9"}, "billions_usd"),
        ({"CL_UNIT": "Index", "UNIT_MULT": "0"}, "index"),
        ({}, "value"),                      # BEA omitted both: do not invent a basis
    ]
    for fields, expected in cases:
        with _with_key("BEA_API_KEY"), \
                mock.patch.object(bea.net, "get_json", return_value=one(**fields)):
            got = bea.fetch_macro_dataset("T10105", year="2026", as_of="2026-10-03").records[0]
        assert got.unit == expected, (fields, got.unit, expected)
    print("ok the BEA metric names its line and the unit is derived, not assumed")


def test_bea_rows_carry_line_and_unit_into_the_series() -> None:
    with _with_key("BEA_API_KEY"), \
            mock.patch.object(bea.net, "get_json", return_value=BEA_PAYLOAD):
        result = bea.fetch_macro_dataset("T10105", year="2026", as_of="2026-10-03")
    columns = result.series["columns"]
    assert "line_number" in columns and "unit" in columns, columns
    lines = {row["line_number"] for row in result.series["rows"]}
    assert lines == {"1", "2"}, lines
    assert all(row["unit"] == "millions_usd" for row in result.series["rows"])
    print("ok the bulk series keeps line and unit per row, so a mixed table stays readable")


def test_rows_are_emittable_with_canonical_postures() -> None:
    with _with_key("FRED_API_KEY"), \
            mock.patch.object(fred.net, "get_json", return_value=FRED_PAYLOAD):
        frec = fred.fetch_macro_series("DGS10", as_of="2026-10-03").records[0]
    with _with_key("BEA_API_KEY"), \
            mock.patch.object(bea.net, "get_json", return_value=BEA_PAYLOAD):
        brec = bea.fetch_macro_dataset("T10101", year="2026", as_of="2026-10-03").records[0]
    for rec, sid in ((frec, "fred_macro_series_api"), (brec, "bea_data_api")):
        assert rec.family == "macro_series"
        assert rec.posture.source_id == sid
        assert rec.posture.authority_level == "L2"
        assert rec.posture.claim_type == "fact"
        assert rec.posture.acquisition_mode == "free_with_key"
        assert rec.metric and rec.claim_text and rec.as_of_date == "2026-10-03"
    print("ok both adapters emit L2 fact macro_series rows carrying their registry source id")


def test_credentials_never_reach_the_recorded_url() -> None:
    """The leak that a live call found and a mocked test would not have.

    The request URL carries the key as a query parameter, and provenance / evidence-log
    URLs are TRACKED artifacts — the first live FRED fetch wrote
    ``api_key=<the real key>`` straight into evidence-log.csv. Redaction is therefore
    asserted on the recorded field, not just on the redaction helper.
    """
    secret = "0123456789abcdef0123456789abcdef"
    with mock.patch.object(config, "api_key", return_value=secret), \
            mock.patch.object(fred.net, "get_json", return_value=FRED_PAYLOAD):
        frec = fred.fetch_macro_series("DGS10", as_of="2026-10-03").records[0]
    assert secret not in frec.url_or_path, frec.url_or_path
    assert "api_key=REDACTED" in frec.url_or_path

    with mock.patch.object(config, "api_key", return_value="11111111-2222-3333-4444-555555555555"), \
            mock.patch.object(bea.net, "get_json", return_value=BEA_PAYLOAD):
        brec = bea.fetch_macro_dataset("T10101", year="2026", as_of="2026-10-03").records[0]
    assert "555555555555" not in brec.url_or_path, brec.url_or_path
    assert "UserID=REDACTED" in brec.url_or_path

    # The helper itself, including the shapes a new adapter might produce.
    r = net.redact_url
    assert r("https://x/y?a=1&api_key=secret&b=2") == "https://x/y?a=1&api_key=REDACTED&b=2"
    assert r("https://x/y?APIKEY=secret") == "https://x/y?APIKEY=REDACTED"
    assert r("https://x/y?key=s&token=t&password=p").count("REDACTED") == 3
    assert r("https://x/y?series_id=GDP") == "https://x/y?series_id=GDP"
    assert r("https://x/y") == "https://x/y" and r("") == ""
    # Already-redacted input stays stable (idempotent), so a re-run cannot double-wrap.
    assert r(r("https://x/y?api_key=secret")) == "https://x/y?api_key=REDACTED"
    print("ok keys are redacted out of the recorded URL, and the helper is idempotent")


def main() -> int:
    test_missing_key_is_catchable_as_fetch_error()
    test_fred_missing_marker_is_dropped_not_zeroed()
    test_fred_opts_out_of_compression()
    test_fred_rejected_key_is_a_key_gap_not_a_retry()
    test_bea_error_inside_http_200_is_not_success()
    test_bea_drops_suppressed_cells_and_normalises_periods()
    test_bea_names_the_line_and_derives_the_unit()
    test_bea_rows_carry_line_and_unit_into_the_series()
    test_rows_are_emittable_with_canonical_postures()
    test_credentials_never_reach_the_recorded_url()
    print("mira_data_us_macro_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
