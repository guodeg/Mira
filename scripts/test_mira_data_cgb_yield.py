#!/usr/bin/env python3
"""Offline tests for the China government bond yield curve adapter (L2, keyless).

This channel closes the A-share equivalent of the US Treasury gap. Cracking its contract took
several failed probes, and **each failure looked like "the source has no data"** rather than like
a rejected query — which is precisely why the tests pin all of it:

1. **``bondType`` must be ``CYCC000``.** ``CYCC001`` (the value the port I read had labelled 国债)
   returns an empty result, as do its neighbours.
2. **``reference=1,2,3`` and ``termId`` are required.** Without them the response is
   ``records: []`` with ``data: None`` — indistinguishable from emptiness unless you know.
3. **``pageSize`` is capped between 50 and 200.** ``1000``/``500``/``200`` each answer **HTTP
   403**, so "ask for everything" is refused rather than truncated.
4. **The window is capped at one month**, and a wider range returns the vendor's own message.
5. **The default tenor set is a filter, not an absence of one** — emitting all 50 points as
   claims buries the curve's shape, so benchmarks are claimed and the full curve goes to series.
6. **Tenor normalisation**: a naive ``rstrip("0")`` renders 5.0 as ``"5"``, which matches no
   published tenor and would silently select nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import net
from tools.mira_data.adapters import chinamoney_curve as cc


def _row(session, term, maturity, current=None, future="---"):
    return {"newDateValueCN": session, "newDateValue": session,
            "yearTermStr": term, "maturityYieldStr": maturity,
            "currentYieldStr": maturity if current is None else current,
            "futureYieldStr": future}


# One session's real curve, trimmed to the benchmark tenors plus a few neighbours.
CURVE_ROWS = [
    _row("2026-09-30", "0.083", "1.0150"), _row("2026-09-30", "0.25", "1.1642"),
    _row("2026-09-30", "0.5", "1.1797"), _row("2026-09-30", "0.75", "1.2000"),
    _row("2026-09-30", "1.0", "1.2219"), _row("2026-09-30", "2.0", "1.2596"),
    _row("2026-09-30", "3.0", "1.2830"), _row("2026-09-30", "5.0", "1.4078"),
    _row("2026-09-30", "7.0", "1.5100"), _row("2026-09-30", "10.0", "1.6830"),
    _row("2026-09-30", "15.0", "1.9504"), _row("2026-09-30", "20.0", "2.0973"),
    _row("2026-09-30", "30.0", "2.0990"),
    # an earlier session also present in page 1, to prove the newest is chosen
    _row("2026-09-29", "10.0", "1.6500"),
]


def _fetch(rows=None, **kwargs):
    payload = {"records": CURVE_ROWS if rows is None else rows,
               "data": {"total": 972, "message": None}}
    with mock.patch.object(cc.net, "get_json", return_value=payload) as getter:
        result = cc.fetch_yield_curve(**kwargs)
    return result, getter


def test_contract_parameters_are_sent_exactly() -> None:
    _, getter = _fetch()
    url = getter.call_args[0][0]
    # Each of these was the difference between data and an empty result.
    assert "bondType=CYCC000" in url, url
    assert "reference=1%2C2%2C3" in url or "reference=1,2,3" in url, url
    assert "termId=1" in url, url
    assert f"pageSize={cc.SAFE_PAGE_SIZE}" in url, url
    assert cc.SAFE_PAGE_SIZE == 50, "200 and above answer HTTP 403"
    print("ok the required contract parameters are sent, at a page size the host accepts")


def test_newest_session_is_claimed_and_defaults_are_a_filter() -> None:
    result, _ = _fetch()
    periods = {r.period for r in result.records}
    # Page 1 carried two sessions; only the newest is the curve to read.
    assert periods == {"2026-09-30"}, periods
    # The default set is a FILTER: 13 rows here, of which the 12 benchmarks are claimed.
    assert len(result.records) == 12, [r.metric for r in result.records]
    by_label = {r.provenance["tenorLabel"]: r for r in result.records}
    assert by_label["10Y"].value == 1.683
    assert by_label["1M"].value == 1.015
    assert by_label["10Y"].provenance["tenorYears"] == 10.0
    assert by_label["10Y"].unit == "percent"
    assert by_label["10Y"].posture.authority_level == "L2"
    assert by_label["10Y"].posture.source_id == "chinamoney_rates_api"
    # The whole curve still travels, so the shape is not lost to the benchmark filter.
    assert len(result.series["rows"]) == len(CURVE_ROWS) - 1, "only the newest session's points"
    print("ok the newest session's benchmark tenors are claimed and the full curve is kept")


def test_explicit_tenors_select_a_subset() -> None:
    result, _ = _fetch(tenors="10,30")
    assert {r.provenance["tenorLabel"] for r in result.records} == {"10Y", "30Y"}
    # A tenor that exists is accepted; one that does not is a labelled gap, not a silent empty.
    try:
        _fetch(tenors="99")
    except net.FetchError as exc:
        message = str(exc)
        assert "cgb_yield_source_gap" in message
        assert "99.0" in message, "the gap should name the requested tenor"
    else:
        raise AssertionError("a tenor absent from the curve must be a labelled gap")
    try:
        cc._parse_tenors("abc")
    except net.FetchError as exc:
        assert "cgb_yield_tenor_gap" in str(exc)
    else:
        raise AssertionError("a non-numeric tenor must be refused before any call")
    print("ok an explicit tenor subset works and an unknown tenor is a labelled gap")


def test_tenor_normalisation_keeps_one_decimal() -> None:
    # A naive rstrip("0") gives "5", which matches no published tenor.
    assert cc._canonical(5.0) == "5.0"
    assert cc._canonical(1.0) == "1.0"
    assert cc._canonical(10.0) == "10.0"
    assert cc._canonical(30.0) == "30.0"
    assert cc._canonical(0.083) == "0.083"
    assert cc._canonical(0.25) == "0.25"
    assert cc._canonical(0.5) == "0.5"
    # And every default tenor must survive a parse/render round trip.
    for term in cc.DEFAULT_TENORS:
        assert cc._canonical(float(term)) == term, term
    assert cc._label("0.083") == "1M" and cc._label("0.75") == "9M"
    assert cc._label("10.0") == "10Y"
    print("ok tenor normalisation round-trips every default and keeps one decimal")


def test_empty_and_dash_values_are_labelled_gaps() -> None:
    # The vendor writes "---" for a point it has no value for; that is not a zero yield.
    assert cc._num("---") is None
    assert cc._num("") is None
    assert cc._num("1.683") == 1.683
    rows = [_row("2026-09-30", "10.0", "---")]
    try:
        _fetch(rows)
    except net.FetchError as exc:
        assert "none carried a parsable maturity yield" in str(exc)
    else:
        raise AssertionError("a curve of only dashes must be a labelled gap")
    # An empty payload with the vendor's own message must surface that message.
    payload = {"records": [], "data": {"message": "只提供一个月历史数据查询。"}}
    with mock.patch.object(cc.net, "get_json", return_value=payload):
        try:
            cc.fetch_yield_curve(as_of="2026-10-04")
        except net.FetchError as exc:
            message = str(exc)
            assert "只提供一个月" in message, message
            assert "wider than" in message, "the gap should explain the window limit"
        else:
            raise AssertionError("an empty payload must be a labelled gap")
    print("ok dash values and an empty payload are labelled gaps carrying the vendor message")


def test_family_wrapper_takes_the_positional_symbol() -> None:
    payload = {"records": CURVE_ROWS, "data": {"total": 972}}
    with mock.patch.object(cc.net, "get_json", return_value=payload):
        assert len(cc.fetch_yield_curve_family("").records) == 12
    with mock.patch.object(cc.net, "get_json", return_value=payload):
        assert len(cc.fetch_yield_curve_family("10").records) == 1
    print("ok the family wrapper takes the positional symbol as the optional tenor list")


def main() -> int:
    test_contract_parameters_are_sent_exactly()
    test_newest_session_is_claimed_and_defaults_are_a_filter()
    test_explicit_tenors_select_a_subset()
    test_tenor_normalisation_keeps_one_decimal()
    test_empty_and_dash_values_are_labelled_gaps()
    test_family_wrapper_takes_the_positional_symbol()
    print("mira_data_cgb_yield_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
