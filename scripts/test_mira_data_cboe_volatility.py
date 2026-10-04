#!/usr/bin/env python3
"""Offline tests for the CBOE volatility-index channel (L2, keyless).

Everything here pins a quirk that was measured live on 2026-10-04, and each one would
otherwise produce a silently wrong or empty answer:

1. **Two CSV shapes.** The VIX family publishes ``DATE,OPEN,HIGH,LOW,CLOSE`` while VVIX, GVZ
   and OVX publish only ``DATE,<SYMBOL>``. A parser that hard-codes "column 5" returns nothing
   for the single-column files rather than failing loudly.
2. **Dates are ``MM/DD/YYYY``.** Reading them as day/month silently shifts every date — and
   ``03/06/2006`` is a valid date either way, so nothing would complain.
3. **The tier is the point of the channel.** VIX was already obtainable as an L5 Yahoo price;
   what makes this L2 is that the *calculating exchange* publishes the file. A record that
   dropped that basis would lose the only reason to prefer it.
4. **An unwired symbol is refused, not guessed.** CBOE publishes far more indices than the
   nine verified here.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import net
from tools.mira_data.adapters import cboe_volatility as cb


OHLC_CSV = """DATE,OPEN,HIGH,LOW,CLOSE
09/29/2026,14.900000,15.500000,14.800000,15.310000
09/30/2026,15.310000,16.200000,15.100000,16.050000
10/01/2026,16.050000,16.400000,15.700000,15.880000
10/02/2026,15.880000,15.950000,15.200000,15.310000
"""

SINGLE_CSV = """DATE,VVIX
09/30/2026,90.120000
10/01/2026,88.400000
10/02/2026,87.020000
"""


def _fetch(text, symbol="VIX", **kwargs):
    with mock.patch.object(cb.net, "get", return_value=text.encode("utf-8")):
        return cb.fetch_volatility_index(symbol, as_of="2026-10-04", **kwargs)


def test_ohlc_shape_reads_the_close_column() -> None:
    result = _fetch(OHLC_CSV)
    rec = result.records[0]
    assert rec.value == 15.31, rec.value
    assert rec.period == "2026-10-02"
    assert rec.provenance["open"] == 15.88
    assert rec.provenance["high"] == 15.95 and rec.provenance["low"] == 15.2
    assert rec.unit == "index_points" and rec.metric == "vix_close"
    assert rec.posture.authority_level == "L2"
    print("ok the OHLC shape yields the close and keeps the day's range")


def test_single_column_shape_does_not_return_empty() -> None:
    result = _fetch(SINGLE_CSV, symbol="VVIX")
    rec = result.records[0]
    # "Column 5" does not exist here; the header must be used to find the value column.
    assert rec.value == 87.02, rec.value
    assert rec.provenance["open"] is None and rec.provenance["high"] is None
    assert "header decides" in rec.provenance["shapeNote"]
    print("ok the single-column shape is read via its header instead of a fixed column")


def test_us_dates_are_not_read_as_day_month() -> None:
    rows = cb._parse_history(OHLC_CSV, "VIX")
    assert [r["date"] for r in rows] == ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"]
    # A genuinely ambiguous date pins the order: 03/06/2006 is 6 March, not 3 June.
    assert cb._us_date("03/06/2006") == "2006-03-06"
    assert cb._us_date("12/31/2026") == "2026-12-31"
    assert cb._us_date("nonsense") == ""
    print("ok MM/DD/YYYY dates are read in US order")


def test_rows_are_sorted_so_the_latest_is_really_latest() -> None:
    shuffled = "DATE,OPEN,HIGH,LOW,CLOSE\n10/02/2026,1,2,0.5,15.31\n09/29/2026,1,2,0.5,14.00\n"
    rec = _fetch(shuffled).records[0]
    # Without the sort, a file whose rows are not in order would publish a stale "latest".
    assert rec.period == "2026-10-02" and rec.value == 15.31
    assert rec.provenance["firstDate"] == "2026-09-29"
    print("ok rows are sorted before the latest observation is chosen")


def test_tier_basis_survives_into_provenance() -> None:
    rec = _fetch(OHLC_CSV).records[0]
    assert rec.posture.source_id == "cboe_volatility_api"
    assert rec.posture.claim_type == "fact"
    # The whole reason to prefer this over the L5 Yahoo quote must travel with the claim.
    assert "calculates the index" in rec.provenance["tierBasis"]
    assert rec.provenance["rowsInFile"] == 4 and rec.provenance["rowsKept"] == 4
    series = _fetch(OHLC_CSV).series
    assert series["name"] == "cboe-VIX" and "value" in series["columns"]
    print("ok the L2 basis and the file shape travel with the claim")


def test_unwired_symbol_is_refused_with_the_wired_list() -> None:
    try:
        cb.fetch_volatility_index("VIXMOON", as_of="2026-10-04")
    except net.FetchError as exc:
        message = str(exc)
        assert "cboe_source_gap" in message
        for wired in ("VIX", "VVIX", "OVX"):
            assert wired in message, (wired, message)
        # It should not silently fetch a different file and label it as the requested one.
        assert "VIXMOON" in message
    else:
        raise AssertionError("an unwired CBOE symbol must be refused")
    # A file that parses to nothing is a gap rather than a zero observation.
    with mock.patch.object(cb.net, "get", return_value=b"DATE,OPEN,HIGH,LOW,CLOSE\n"):
        try:
            cb.fetch_volatility_index("VIX", as_of="2026-10-04")
        except net.FetchError as exc:
            assert "parsed to no usable observation" in str(exc)
        else:
            raise AssertionError("an empty history file must be a gap")
    print("ok an unwired symbol and an empty file are both labelled gaps")


def test_every_wired_symbol_is_described() -> None:
    for symbol, (described, underlying) in cb.SERIES.items():
        assert described and underlying, symbol
    assert len(cb.SERIES) == 9, sorted(cb.SERIES)
    print("ok all nine wired volatility indices carry a description and an underlying")


def main() -> int:
    test_ohlc_shape_reads_the_close_column()
    test_single_column_shape_does_not_return_empty()
    test_us_dates_are_not_read_as_day_month()
    test_rows_are_sorted_so_the_latest_is_really_latest()
    test_tier_basis_survives_into_provenance()
    test_unwired_symbol_is_refused_with_the_wired_list()
    test_every_wired_symbol_is_described()
    print("mira_data_cboe_volatility_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
