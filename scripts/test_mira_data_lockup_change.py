#!/usr/bin/env python3
"""Offline tests for the 限售股份变动情况 channel (L1, filed report body).

This parser has one job that makes it worth having: recover a table the plain text layer
destroys, without ever publishing a plausible-looking wrong number. The tests therefore pin
the two failure modes that actually bit during development:

1. **Column order.** The first working draft unpacked the numeric columns right-to-left and
   produced opening=0 / added=649,900,000 for a row the filing states as
   opening=649,900,000 / added=0. It looked entirely reasonable. The published 合计 row caught
   it, which is why the checksum is mandatory rather than best-effort.
2. **A silently skipped checksum.** The same draft emitted whenever the check dict was empty,
   so a table whose 合计 row failed to parse went out unvalidated. Now "no verified rollup" is
   itself a refusal.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data.adapters import cninfo_disclosure as cn


# Verbatim from 海光信息 2025 annual report (688041), page 103 — the row the adapter must get
# right, plus the 合计 row whose two leading cells layout mode glues together.
HAIGUANG_ROWS = [
    ["中科曙光", "649,900,000", "649,900,000", "0", "0", "首发限售", "2025/8/12"],
    ["海富天鼎合伙", "251,194,546", "251,194,546", "0", "0", "首发限售", "2025/8/12"],
    ["成都产投有限", "167,600,000", "167,600,000", "0", "0", "首发限售", "2025/8/12"],
    ["蓝轻舟合伙", "141,486,364", "141,486,364", "0", "0", "首发限售", "2025/8/12"],
    ["成都高投有限", "137,600,000", "137,600,000", "0", "0", "首发限售", "2025/8/12"],
    ["成都集萃有限", "90,000,000", "90,000,000", "0", "0", "首发限售", "2025/8/12"],
    ["合计", "1,437,780,9101,437,780,910", "0", "0", "/", "/"],
]
HAIGUANG_TOTAL = 1_437_780_910


def test_columns_are_read_in_published_order() -> None:
    parsed = cn.parse_lockup_change_rows(HAIGUANG_ROWS)
    assert len(parsed["holders"]) == 6, parsed["holders"]
    first = parsed["holders"][0]
    assert first["holder"] == "中科曙光"
    # The exact values the filing prints, in the filing's column order.
    assert first["opening"] == 649_900_000
    assert first["released"] == 649_900_000
    assert first["added"] == 0
    assert first["closing"] == 0
    assert first["reason"] == "首发限售"
    assert first["date"] == "2025-08-12"
    # The right-to-left misread this test exists to prevent.
    assert first["opening"] != 0 and first["added"] != 649_900_000
    print("ok columns are read in published order (opening=649.9m, added=0, not reversed)")


def test_rollup_validates_the_parse() -> None:
    parsed = cn.parse_lockup_change_rows(HAIGUANG_ROWS)
    assert parsed["rollup"] is not None, "the 合计 row must be recovered, not skipped"
    assert parsed["rollup"]["opening"] == HAIGUANG_TOTAL
    assert parsed["rollup"]["released"] == HAIGUANG_TOTAL
    assert parsed["checks"], "a parsed rollup must produce checks"
    assert all(parsed["checks"].values()), parsed["checks"]
    # Every column of the six holders really does sum to the published total.
    for key, total in (("opening", HAIGUANG_TOTAL), ("released", HAIGUANG_TOTAL),
                       ("added", 0), ("closing", 0)):
        assert sum(h[key] for h in parsed["holders"]) == total
    print("ok the 合计 row is recovered and every column sums to it")


def test_glued_total_is_split_and_ambiguous_ones_are_refused() -> None:
    # Layout mode glues the two leading 合计 cells; the splitter must repair it.
    assert cn._split_glued_numbers("1,437,780,9101,437,780,910", [649_900_000.0]) == \
        [1_437_780_910.0, 1_437_780_910.0]
    # A correctly formatted number is never mistaken for a glued pair.
    assert cn._split_glued_numbers("1,437,780,910", []) == []
    # Malformed halves must not be accepted as numbers: this is what once turned one token
    # into 14 bogus candidates (`'1,'` and `',437'` both passed a naive digit test).
    assert cn._cell_number("1,437,780,9101,437,780,910") is None
    assert cn._cell_number("1,") is None and cn._cell_number(",437") is None
    assert cn._cell_number("1,437,780,910") == 1_437_780_910.0
    assert cn._cell_number("-5,021,413") == -5_021_413.0
    # Genuinely ambiguous splits return nothing rather than guessing: `1234` could be 1|234,
    # 12|34 or 123|4 and none of them is evidentially favoured.
    assert cn._split_glued_numbers("1234", []) == []
    print("ok the glued 合计 cell is repaired, and malformed numbers are rejected")


def test_a_missing_rollup_is_a_gap_not_a_silent_pass() -> None:
    # The 合计 row is unreadable, so nothing can validate the holder rows.
    rows = [r for r in HAIGUANG_ROWS if r[0] != "合计"]
    rows.append(["合计", "not-a-number", "0", "0", "/", "/"])
    parsed = cn.parse_lockup_change_rows(rows)
    assert parsed["holders"], "holder rows are still parsed"
    assert parsed["rollup"] is None
    assert not parsed["checks"], "no rollup means no checks at all"
    assert any("合计" in gap for gap in parsed["gaps"])
    print("ok an unreadable 合计 row yields no checks, which the caller must treat as fatal")


def test_inconsistent_rows_are_reported_not_averaged() -> None:
    rows = [
        ["某股东", "1,000", "400", "0", "500", "股权激励", "2026/1/5"],   # 1000-400 != 500
        ["合计", "1,000", "400", "0", "500", "/", "/"],
    ]
    parsed = cn.parse_lockup_change_rows(rows)
    assert any("某股东" in gap and "年末" in gap for gap in parsed["gaps"]), parsed["gaps"]
    # A row that does balance produces no gap.
    ok = cn.parse_lockup_change_rows(
        [["某股东", "1,000", "400", "0", "600", "股权激励", "2026/1/5"],
         ["合计", "1,000", "400", "0", "600", "/", "/"]])
    assert ok["gaps"] == [], ok["gaps"]
    assert all(ok["checks"].values()), ok["checks"]
    print("ok an unbalanced row is named as a gap, and a balanced one is not")


def test_table_locator_anchors_on_the_section_and_stops_at_the_next_one() -> None:
    page = "\n".join([
        "(二) 限售股份变动情况",
        "√适用  □不适用",
        "                                                          单位：股",
        " 股东名称       年初限售股数      本年解除限售     本年增加    年末限    限售原因    解除限售",
        "                           股数      限售股数    售股数              日期",
        " 中科曙光         649,900,000 649,900,000   0      0  首发限售    2025/8/12",
        "   合计        1,437,780,9101,437,780,910 0      0     /       /",
        "",
        "二、证券发行与上市情况",
        "(一)截至报告期内证券发行情况",
        " 香港中央结算有限公司 47,137,611 人民币普通股 47,137,611",
    ])
    rows = cn.lockup_table_lines(page)
    assert [r[0] for r in rows] == ["中科曙光", "合计"], rows
    assert "香港中央结算" not in "".join("".join(r) for r in rows), "must stop at the next section"

    # A page without the table yields nothing rather than whatever table it does have.
    assert cn.lockup_table_lines("前十名股东持股情况\n香港中央结算有限公司 47,137,611") == []
    print("ok the locator anchors on the section, keeps the row order, and stops at the next one")


def test_dates_and_reasons_are_recognised_conservatively() -> None:
    assert cn._cell_date("2025/8/12") == "2025-08-12"
    assert cn._cell_date("2025-08-12") == "2025-08-12"
    assert cn._cell_date("2025年8月12日") == "2025-08-12"
    assert cn._cell_date("/") == "" and cn._cell_date("0") == ""
    assert cn._lockup_reason("首发限售") == "首发限售"
    assert cn._lockup_reason("股权激励") == "股权激励"
    # A bare word with no lockup vocabulary is not treated as a reason.
    assert cn._lockup_reason("人民币普通股") == ""
    assert cn._lockup_reason("/") == ""
    print("ok dates parse in three formats and reasons need the lockup vocabulary")


def main() -> int:
    test_columns_are_read_in_published_order()
    test_rollup_validates_the_parse()
    test_glued_total_is_split_and_ambiguous_ones_are_refused()
    test_a_missing_rollup_is_a_gap_not_a_silent_pass()
    test_inconsistent_rows_are_reported_not_averaged()
    test_table_locator_anchors_on_the_section_and_stops_at_the_next_one()
    test_dates_and_reasons_are_recognised_conservatively()
    print("mira_data_lockup_change_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
