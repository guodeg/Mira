#!/usr/bin/env python3
"""Offline tests for the industry-output series added to the official statistics channel.

The L2 review found that most association-published *production* figures are already official
statistics, so they ship on the existing channel. These tests pin the seven new series and, more
importantly, pin that they are honest about which axis they cover: a series that reports 产量
must not be presented as 销量/装机, and the quarterly capacity-utilisation series must stay
quarterly.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import net
from tools.mira_data.adapters import nbs_stats as nbs


EXPECTED = {
    "AUTO_OUTPUT": ("auto_output", "10k_vehicles", "monthly"),
    "NEV_OUTPUT": ("new_energy_vehicle_output", "10k_vehicles", "monthly"),
    "CRUDE_STEEL": ("crude_steel_output", "10k_tonnes", "monthly"),
    "STEEL_PRODUCTS": ("steel_products_output", "10k_tonnes", "monthly"),
    "NONFERROUS_OUTPUT": ("nonferrous_output", "10k_tonnes", "monthly"),
    "POWER_GENERATION": ("power_generation", "100m_kwh", "monthly"),
    "CAPACITY_UTILIZATION": ("capacity_utilization", "percent", "quarterly"),
}


def test_series_are_registered_with_their_own_catalog() -> None:
    for name, (metric, unit, frequency) in EXPECTED.items():
        series = nbs.SERIES.get(name)
        assert series is not None, f"{name} is not registered"
        assert series.frequency == frequency, (name, series.frequency)
        assert len(series.metrics) == 1, (name, "one indicator per catalog in this group")
        got = series.metrics[0]
        assert got.name == metric, (name, got.name)
        assert got.unit == unit, (name, got.unit)
        assert len(series.cid) == 32 and len(got.indicator_id) == 32, name
    print("ok seven industry series are registered with the right metric, unit and frequency")


def test_records_declare_the_production_axis_only() -> None:
    """A 产量 series must not be labelled as sales, installations or inventory.

    Checked on the registered series themselves rather than through mocked internals, so the rule
    holds no matter how the read path is refactored.
    """
    for name in EXPECTED:
        series = nbs.SERIES[name]
        text = series.label + " " + " ".join(m.name for m in series.metrics)
        for forbidden in ("销量", "装机", "库存", "开工率"):
            assert forbidden not in text, (name, forbidden, text)
    assert nbs.SERIES["AUTO_OUTPUT"].label == "汽车产量"
    assert nbs.SERIES["CAPACITY_UTILIZATION"].frequency == "quarterly", (
        "capacity utilisation is published quarterly and must not be offered as monthly")
    print("ok the new series claim the production axis only, and quarterly stays quarterly")

    # The live shape verified on 2026-10-03: 汽车产量 269.7 万辆 (2026-08),
    # 新能源汽车产量 164.7 万辆 (2026-08), 工业产能利用率 73.0 % (2026Q2).
    assert nbs.SERIES["AUTO_OUTPUT"].metrics[0].unit == "10k_vehicles"
    assert nbs.SERIES["POWER_GENERATION"].metrics[0].unit == "100m_kwh"


def main() -> int:
    test_series_are_registered_with_their_own_catalog()
    test_records_declare_the_production_axis_only()
    print("mira_data_nbs_industry_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
