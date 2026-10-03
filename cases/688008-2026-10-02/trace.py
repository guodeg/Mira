"""Trace the 2026 peak-to-trough path and share-count / capital-structure evidence."""
import json
import os
from datetime import datetime, timezone, timedelta

RAW = r"D:\quant\mira\analysis\688008-2026-10-02\raw"
CST = timezone(timedelta(hours=8))


def load_history(name):
    with open(os.path.join(RAW, name), "r", encoding="utf-8") as f:
        env = json.load(f)
    rows = []
    for r in env["data"]["item"]:
        d = datetime.fromtimestamp(r["date_ms"] / 1000, tz=CST).strftime("%Y-%m-%d")
        rows.append({"date": d, "close": float(r["close_price"]), "high": float(r["high_price"]),
                     "low": float(r["low_price"]), "turnover": float(r["turnover"]),
                     "volume": int(r["volume"])})
    rows.sort(key=lambda x: x["date"])
    return rows


raw = load_history("history_raw.json")

print("=" * 100)
print("2026 PATH: monthly summary (raw prices)")
print("=" * 100)
by_month = {}
for r in raw:
    by_month.setdefault(r["date"][:7], []).append(r)
for m in sorted(by_month):
    if m < "2025-06":
        continue
    rows = by_month[m]
    print(f"{m}: open={rows[0]['close']:8.2f} high={max(x['high'] for x in rows):8.2f} "
          f"low={min(x['low'] for x in rows):8.2f} close={rows[-1]['close']:8.2f}  "
          f"chg={(rows[-1]['close']/rows[0]['close']-1)*100:+7.2f}%  days={len(rows)}")

print()
print("=" * 100)
print("DAILY PATH AROUND THE 2026-07-01 PEAK (raw)")
print("=" * 100)
seg = [r for r in raw if "2026-06-15" <= r["date"] <= "2026-07-31"]
for r in seg:
    print(f"{r['date']}  high={r['high']:8.2f}  low={r['low']:8.2f}  close={r['close']:8.2f}  "
          f"turnover={r['turnover']/1e8:7.2f}亿")

print()
print("=" * 100)
print("DAILY PATH: 2026-09 (most recent month)")
print("=" * 100)
seg = [r for r in raw if r["date"] >= "2026-09-01"]
for r in seg:
    print(f"{r['date']}  high={r['high']:8.2f}  low={r['low']:8.2f}  close={r['close']:8.2f}  "
          f"turnover={r['turnover']/1e8:7.2f}亿")

print()
print("=" * 100)
print("SHARE COUNT PROXY")
print("=" * 100)
# turnover / volume gives approx average trade price; volume is in shares (股)
for r in raw[-5:]:
    vwap = r["turnover"] / r["volume"]
    print(f"{r['date']}  volume={r['volume']:>12,d}  turnover={r['turnover']:>18,.0f}  "
          f"implied avg price={vwap:7.3f}  close={r['close']:.2f}")
