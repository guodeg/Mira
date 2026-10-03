"""Technical + valuation analytics for 688008.SH from local hithink-finance history."""
import json
import math
import os
from datetime import datetime, timezone, timedelta

RAW = r"D:\quant\mira\analysis\688008-2026-10-02\raw"
OUT = r"D:\quant\mira\analysis\688008-2026-10-02"
CST = timezone(timedelta(hours=8))


def load_history(name):
    with open(os.path.join(RAW, name), "r", encoding="utf-8") as f:
        env = json.load(f)
    rows = []
    for r in env["data"]["item"]:
        d = datetime.fromtimestamp(r["date_ms"] / 1000, tz=CST).strftime("%Y-%m-%d")
        rows.append({
            "date": d, "close": float(r["close_price"]), "open": float(r["open_price"]),
            "high": float(r["high_price"]), "low": float(r["low_price"]),
            "volume": int(r["volume"]), "turnover": float(r["turnover"]),
        })
    rows.sort(key=lambda x: x["date"])
    return rows


raw = load_history("history_raw.json")
fwd = load_history("history_forward.json")

print(f"RAW   rows={len(raw)}  {raw[0]['date']} -> {raw[-1]['date']}")
print(f"FWD   rows={len(fwd)}  {fwd[0]['date']} -> {fwd[-1]['date']}")
print(f"Last raw close = {raw[-1]['close']}  on {raw[-1]['date']}")
print()

# ---------- calendar-year performance (forward-adjusted = total return incl. dividends) ----------
print("=" * 96)
print("CALENDAR YEAR TOTAL RETURN (forward-adjusted, incl. dividends)")
print("=" * 96)
by_year = {}
for r in fwd:
    by_year.setdefault(r["date"][:4], []).append(r)

prev_close = None
for y in sorted(by_year):
    rows = by_year[y]
    start = prev_close if prev_close is not None else rows[0]["close"]
    end = rows[-1]["close"]
    ret = (end / start - 1) * 100
    hi = max(x["high"] for x in rows)
    lo = min(x["low"] for x in rows)
    print(f"{y}:  start={start:8.2f}  end={end:8.2f}  return={ret:8.2f}%  "
          f"high={hi:8.2f}  low={lo:8.2f}  days={len(rows)}")
    prev_close = end
print()

# ---------- trailing returns ----------
print("=" * 96)
print("TRAILING TOTAL RETURN (forward-adjusted)")
print("=" * 96)
last = fwd[-1]
last_date = datetime.strptime(last["date"], "%Y-%m-%d")
for label, days in [("1M", 30), ("3M", 91), ("6M", 182), ("1Y", 365), ("2Y", 730), ("3Y", 1095), ("5Y", 1826)]:
    target = (last_date - timedelta(days=days)).strftime("%Y-%m-%d")
    prior = [r for r in fwd if r["date"] <= target]
    if not prior:
        print(f"{label:3s}: no data before {target}")
        continue
    base = prior[-1]
    ret = (last["close"] / base["close"] - 1) * 100
    print(f"{label:3s}: base {base['date']} close={base['close']:8.2f}  ->  "
          f"{last['date']} close={last['close']:8.2f}   return={ret:9.2f}%")
print()

# ---------- drawdown ----------
print("=" * 96)
print("DRAWDOWN ANALYSIS")
print("=" * 96)
for label, days in [("3Y", 1095), ("5Y", 1826), ("all", 9999)]:
    target = (last_date - timedelta(days=days)).strftime("%Y-%m-%d")
    seg = [r for r in fwd if r["date"] >= target] or fwd
    peak = -1e9
    peak_date = None
    mdd = 0.0
    mdd_peak = mdd_trough = None
    for r in seg:
        if r["close"] > peak:
            peak = r["close"]
            peak_date = r["date"]
        dd = r["close"] / peak - 1
        if dd < mdd:
            mdd = dd
            mdd_peak = peak_date
            mdd_trough = r["date"]
    hi = max(r["high"] for r in seg)
    lo = min(r["low"] for r in seg)
    print(f"{label:4s} window {seg[0]['date']} -> {seg[-1]['date']}")
    print(f"      max drawdown = {mdd*100:7.2f}%  from {mdd_peak} peak {peak if False else ''}to {mdd_trough}")
    print(f"      period high={hi:8.2f}   period low={lo:8.2f}   final={seg[-1]['close']:8.2f}")
    pctile = sum(1 for r in seg if r["close"] <= seg[-1]["close"]) / len(seg) * 100
    print(f"      current price percentile within window = {pctile:5.1f}%")
    print()

# ---------- all-time high ----------
ath = max(fwd, key=lambda r: r["high"])
print(f"ALL-TIME HIGH (intraday, fwd-adjusted): {ath['high']:.2f} on {ath['date']}")
ath_close = max(fwd, key=lambda r: r["close"])
print(f"ALL-TIME HIGH (close, fwd-adjusted):    {ath_close['close']:.2f} on {ath_close['date']}")
print(f"Current {last['close']:.2f} is {(last['close']/ath_close['close']-1)*100:.2f}% vs ATH close")
print()

# ---------- moving averages ----------
print("=" * 96)
print("MOVING AVERAGES (forward-adjusted)")
print("=" * 96)
closes = [r["close"] for r in fwd]
for n in [5, 10, 20, 60, 120, 250]:
    if len(closes) >= n:
        ma = sum(closes[-n:]) / n
        print(f"MA{n:<4d} = {ma:8.2f}   price/MA = {(last['close']/ma-1)*100:+7.2f}%")
print()

# ---------- volatility ----------
print("=" * 96)
print("VOLATILITY (forward-adjusted daily log returns)")
print("=" * 96)
def vol(seg, label):
    rets = [math.log(seg[i]["close"] / seg[i-1]["close"]) for i in range(1, len(seg))]
    n = len(rets)
    mean = sum(rets) / n
    var = sum((x - mean) ** 2 for x in rets) / (n - 1)
    sd = math.sqrt(var)
    print(f"{label:12s} n={n:5d}  daily sd={sd*100:6.3f}%  annualized={sd*math.sqrt(252)*100:7.2f}%")
    return sd * math.sqrt(252)

vol(fwd[-250:], "last 250d")
vol(fwd[-500:], "last 500d")
vol(fwd[-1000:], "last 1000d")
vol(fwd, "full")
print()

# ---------- turnover / liquidity ----------
print("=" * 96)
print("LIQUIDITY")
print("=" * 96)
for n in [20, 60, 250]:
    seg = raw[-n:]
    avg_to = sum(r["turnover"] for r in seg) / len(seg)
    avg_vol = sum(r["volume"] for r in seg) / len(seg)
    print(f"last {n:3d}d: avg turnover = {avg_to/1e8:8.2f} 亿元/day   avg volume = {avg_vol/1e4:10.0f} 万股/day")
print()

# ---------- annual OHLC table ----------
print("=" * 96)
print("KEY PRICE LEVELS BY YEAR (raw, unadjusted)")
print("=" * 96)
by_year_raw = {}
for r in raw:
    by_year_raw.setdefault(r["date"][:4], []).append(r)
for y in sorted(by_year_raw):
    rows = by_year_raw[y]
    print(f"{y}: open={rows[0]['open']:8.2f} high={max(x['high'] for x in rows):8.2f} "
          f"low={min(x['low'] for x in rows):8.2f} close={rows[-1]['close']:8.2f}")
