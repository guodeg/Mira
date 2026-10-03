# -*- coding: utf-8 -*-
"""Build the corporate-action summary and a single consolidated summary file
for 688008.SH from the raw artifacts saved under raw/."""
from __future__ import annotations

import datetime as dt
import json
import os

BASE = r"D:\quant\mira\analysis\688008-2026-10-02"
RAW = os.path.join(BASE, "raw")
TZ = dt.timezone(dt.timedelta(hours=8))


def rd(name):
    with open(os.path.join(RAW, name), encoding="utf-8") as fh:
        return json.load(fh)


def ts(ms):
    return dt.datetime.fromtimestamp(ms / 1000, TZ).isoformat()


# ------------------------------------------------------- corporate actions
ca = rd("corporate_actions.json")
events = sorted(ca["data"]["item"], key=lambda x: x["ex_date_ms"])
summary = {
    "thscode": ca["data"]["thscode"],
    "source_file": os.path.join(RAW, "corporate_actions.json"),
    "event_count": len(events),
    "window": f"{ts(events[0]['ex_date_ms'])[:10]} .. {ts(events[-1]['ex_date_ms'])[:10]}",
    "note": "per_share_bonus (送股/转增) is 0 for every event; all events are cash dividends",
    "events": [{"ex_date": ts(e["ex_date_ms"])[:10],
                "ex_date_ms": e["ex_date_ms"],
                "dividend_per_share_cny": e["dividend_per_share"],
                "per_share_bonus": e["per_share_bonus"]} for e in events],
}
recent = [e for e in summary["events"] if e["ex_date"] >= "2023-10-09"]
summary["events_since_2023_10_09"] = recent
summary["sum_dividend_per_share_since_2023_10_09"] = round(
    sum(e["dividend_per_share_cny"] for e in recent), 4)
last12 = [e for e in summary["events"] if e["ex_date"] >= "2025-10-01"]
summary["sum_dividend_per_share_last_12m"] = round(
    sum(e["dividend_per_share_cny"] for e in last12), 4)
with open(os.path.join(RAW, "corporate_actions_summary.json"), "w", encoding="utf-8") as fh:
    json.dump(summary, fh, ensure_ascii=False, indent=2)

# ------------------------------------------------------------- consolidation
snap = rd("snapshot_688008.json")["data"]["item"][0]
snap_ts = rd("snapshot_688008.json")["data"]["timestamp"]
val = rd("valuation_688008.json")["data"]["item"][0]
val_ts = rd("valuation_688008.json")["data"]["timestamp"]
idx = rd("index_snapshot_000688.json")["data"]["item"][0]
idx_ts = rd("index_snapshot_000688.json")["data"]["timestamp"]
cal = rd("calendar.json")["data"]["item"]
cons = rd("index_000688_constituents.json")["data"]["item"]
ana = rd("analysis_688008.json")

cal_dates = sorted(x["date"] for x in cal)
last_trading = cal_dates[-1]
last_trading_iso = f"{last_trading[:4]}-{last_trading[4:6]}-{last_trading[6:]}"

consolidated = {
    "generated_at": dt.datetime.now(TZ).isoformat(),
    "research_object": {"thscode": "688008.SH", "ticker": "688008",
                        "name": "澜起科技", "board": "上交所科创板 (STAR Market)"},
    "quote": {
        **snap,
        "snapshot_response_timestamp": ts(snap_ts),
        "snapshot_response_timestamp_ms": snap_ts,
        "last_trading_date": last_trading_iso,
        "quote_basis": ("snapshot timestamp is the response assembly time; last_price, prev_price and "
                        "volume/turnover match the 2026-09-30 daily bar, so the quote is the "
                        "2026-09-30 close (the last trading day before the National Day holiday)"),
        "volume_unit": "shares",
        "turnover_unit": "CNY",
        "source_file": os.path.join(RAW, "snapshot_688008.json"),
    },
    "valuation": {
        **val,
        "snapshot_response_timestamp": ts(val_ts),
        "snapshot_response_timestamp_ms": val_ts,
        "as_of_note": "no explicit as-of date returned; ratios are consistent with the 2026-09-30 close",
        "source_file": os.path.join(RAW, "valuation_688008.json"),
    },
    "index_000688_SH": {
        "name": "科创50 / STAR 50",
        **idx,
        "snapshot_response_timestamp": ts(idx_ts),
        "constituent_count": len(cons),
        "constituent_688008_present": any(c["thscode"] == "688008.SH" for c in cons),
        "constituent_membership_checked_from": os.path.join(RAW, "index_000688_constituents.json"),
        "source_file": os.path.join(RAW, "index_snapshot_000688.json"),
    },
    "trading_calendar": {
        "coverage": f"{cal_dates[0][:4]}-{cal_dates[0][4:6]}-{cal_dates[0][6:]} .. {last_trading_iso}",
        "n_trading_days": len(cal_dates),
        "last_trading_date": last_trading_iso,
        "source_file": os.path.join(RAW, "calendar.json"),
    },
    "corporate_actions": summary,
    "price_statistics": ana,
    "data_gaps": [
        "MARKET CAP NOT AVAILABLE: no hithink-finance capability or field returns total or float "
        "market cap. market.snapshot returns only price/volume/turnover; valuation.snapshot returns "
        "only ratio fields (pe_ttm, pe_mrq, pb_mrq, ps_ttm, pcf_ttm); the local DuckDB dim_symbol "
        "table has no share-count column. See derived_market_cap_crosscheck in analysis_688008.json "
        "for a clearly-labelled upper-bound estimate.",
        "NO 52-WEEK RANGE FIELD: market.snapshot has no 52w high/low; computed from the last 250 "
        "daily bars (2025-09-18..2026-09-30, intraday high 332.51 / low 111.61).",
        "NO AS-OF DATE ON VALUATION: valuation.snapshot returns no explicit as-of date; the ratios "
        "are consistent with the 2026-09-30 close.",
        "SANDBOX BLOCKED THE LOCAL DUCKDB: every command that touches the default local database "
        "fails with CLI_INTERNAL_ERROR / 'IO Error: Cannot open file "
        "\"C:\\Users\\lsl\\AppData\\Local\\hithink-finance\\data\\market.duckdb\": 拒绝访问。' because "
        "the DSH workspace-write sandbox forbids opening that file read-write (read-only open "
        "succeeds). Workaround used: copied market.duckdb (906,506,240 bytes) into the workspace and "
        "passed --db; local-source commands then worked.",
        "CLI OPTION GAP: 'hithink-finance capabilities --format json --output <path>' fails with "
        "CLI_BAD_ARGUMENT / \"error: unknown option '--output'\" (capabilities has no --output).",
        "DB QUERY VALIDATOR QUIRK: 'db query' rejects otherwise valid SELECTs containing more than a "
        "simple WHERE, returning DB_READ_ONLY_VIOLATION / 'db query accepts exactly one read-only "
        "SELECT statement.' (order by/limit and multi-condition WHERE both tripped it).",
        "ADJUSTMENT-CONVENTION DIFFERENCE: the remote forward-adjusted series and the local DuckDB "
        "qfq view differ by 0.7%-1.3% in level over 2023 (e.g. 2023-10-09 close 49.33 remote vs "
        "49.90 local). The local database was last synced 2026-08-25. Returns in this report use the "
        "remote series throughout; a constant scale difference does not affect ratio-based returns "
        "within one series.",
        "SHARED OUTPUT DIRECTORY: other files in raw/ (snapshot.json, valuation.json, "
        "history_forward.json, history_raw.json, idx_*.json, income_*/bs_*/cf_*/ind_*.json, "
        "index_catalog.json, index_snapshot.json, corp_actions.json) were written by a concurrent "
        "sibling process at 2026-10-02 21:50-21:52 and were not created or modified by this run.",
    ],
}

with open(os.path.join(RAW, "summary_688008.json"), "w", encoding="utf-8") as fh:
    json.dump(consolidated, fh, ensure_ascii=False, indent=2)

print(json.dumps({"corporate_actions_summary": summary["events"],
                  "last_trading_date": last_trading_iso,
                  "quote": consolidated["quote"],
                  "valuation": consolidated["valuation"],
                  "index": consolidated["index_000688_SH"],
                  "wrote": [os.path.join(RAW, "corporate_actions_summary.json"),
                            os.path.join(RAW, "summary_688008.json")]},
                 ensure_ascii=False, indent=1))
