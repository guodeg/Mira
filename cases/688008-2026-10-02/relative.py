"""Relative performance: 688008.SH vs STAR50 / chip / AI / memory / CSI300 indices."""
import json
import os
from datetime import datetime, timezone, timedelta

RAW = r"D:\quant\mira\analysis\688008-2026-10-02\raw"
CST = timezone(timedelta(hours=8))


def load(name, key="item"):
    with open(os.path.join(RAW, name), "r", encoding="utf-8") as f:
        env = json.load(f)
    rows = []
    for r in env["data"][key]:
        ms = r.get("date_ms") or r.get("date")
        d = datetime.fromtimestamp(ms / 1000, tz=CST).strftime("%Y-%m-%d")
        rows.append({"date": d, "close": float(r.get("close_price") or r.get("last_price"))})
    rows.sort(key=lambda x: x["date"])
    return rows


stock = load("history_raw.json")
series = {
    "688008.SH": stock,
    "科创50 (000688.SH)": load("idx_star50.json"),
    "芯片概念 (885756.TI)": load("idx_chip.json"),
    "人工智能 (885728.TI)": load("idx_ai.json"),
    "存储芯片 (886042.TI)": load("idx_mem.json"),
    "沪深300 (000300.SH)": load("idx_csi300.json"),
}

# align on common dates
common = set(r["date"] for r in stock)
for name, rows in series.items():
    common &= set(r["date"] for r in rows)
common = sorted(common)
print(f"Common trading dates: {len(common)}  {common[0]} -> {common[-1]}")
print()

norm = {}
for name, rows in series.items():
    m = {r["date"]: r["close"] for r in rows}
    base = m[common[0]]
    norm[name] = {d: m[d] / base * 100 for d in common}

print("=" * 110)
print("RELATIVE PERFORMANCE (indexed to 100 at 2025-01-02, using close price)")
print("=" * 110)
hdr = f"{'date':<12}" + "".join(f"{k:>22}" for k in series)
print(hdr)
print("-" * len(hdr))
for d in common:
    if d.endswith(("-01-02", "-03-31", "-06-30", "-09-30", "-12-31")) or d == common[-1]:
        print(f"{d:<12}" + "".join(f"{norm[k][d]:>22.1f}" for k in series))
print()

print("=" * 110)
print("PERIOD RETURNS (%) — 688008 vs benchmarks")
print("=" * 110)
def ret(name, d0, d1):
    a, b = norm[name].get(d0), norm[name].get(d1)
    if a is None or b is None:
        return None
    return (b / a - 1) * 100

periods = []
for label, days in [("1M", 30), ("3M", 91), ("6M", 182), ("1Y", 365)]:
    end = datetime.strptime(common[-1], "%Y-%m-%d")
    tgt = (end - timedelta(days=days)).strftime("%Y-%m-%d")
    cand = [d for d in common if d <= tgt]
    if cand:
        periods.append((label, cand[-1], common[-1]))
periods.append(("YTD26", common[0], common[-1]))

hdr = f"{'period':<8}{'from':<12}{'to':<12}" + "".join(f"{k[:18]:>20}" for k in series)
print(hdr)
print("-" * len(hdr))
for label, d0, d1 in periods:
    line = f"{label:<8}{d0:<12}{d1:<12}"
    for k in series:
        v = ret(k, d0, d1)
        line += f"{v:>20.1f}" if v is not None else f"{'n/a':>20}"
    print(line)
print()

print("=" * 110)
print("BETA / CORRELATION vs 科创50 (daily returns, common window)")
print("=" * 110)
import math
def daily_returns(rows_map, dates):
    return [math.log(rows_map[dates[i]] / rows_map[dates[i-1]]) for i in range(1, len(dates))]

bench_key = "科创50 (000688.SH)"
b = daily_returns(norm[bench_key], common)
for k in series:
    if k == bench_key:
        continue
    s = daily_returns(norm[k], common)
    n = len(s)
    mb = sum(b) / n
    ms = sum(s) / n
    cov = sum((s[i]-ms)*(b[i]-mb) for i in range(n)) / (n-1)
    vb = sum((x-mb)**2 for x in b) / (n-1)
    vs = sum((x-ms)**2 for x in s) / (n-1)
    beta = cov / vb
    corr = cov / math.sqrt(vb*vs)
    print(f"{k:<24} beta={beta:5.2f}  corr={corr:5.2f}  ann.vol={math.sqrt(vs*252)*100:6.1f}%")
