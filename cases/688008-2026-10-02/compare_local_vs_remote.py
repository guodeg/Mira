# -*- coding: utf-8 -*-
"""Cross-check the remote forward-adjusted daily series against the local
DuckDB forward-adjusted series (source=local) for the 2023 calendar year."""
import json

REMOTE = r"D:\quant\mira\analysis\688008-2026-10-02\raw\history_daily_2023_boundary_check.json"
MAIN = r"D:\quant\mira\analysis\688008-2026-10-02\raw\history_daily.json"

import datetime as dt
TZ = dt.timezone(dt.timedelta(hours=8))

loc = json.load(open(REMOTE, encoding="utf-8"))["data"]
loc_map = {x["date"]: x["close"] for x in loc}

main = json.load(open(MAIN, encoding="utf-8"))["data"]["item"]
rem_map = {dt.datetime.fromtimestamp(x["date_ms"] / 1000, TZ).date().isoformat(): x["close_price"]
           for x in main}

common = sorted(set(loc_map) & set(rem_map))
print(f"overlapping dates in 2023: {len(common)}")
ratios = [(d, loc_map[d] / rem_map[d]) for d in common]
print("first:", ratios[0], " last:", ratios[-1])
rs = [r for _, r in ratios]
print(f"ratio local/remote: min={min(rs):.6f} max={max(rs):.6f} spread={max(rs)-min(rs):.8f}")
for d, r in ratios[::40]:
    print(f"  {d}  local={loc_map[d]:.4f} remote={rem_map[d]:.4f} ratio={r:.6f}")
