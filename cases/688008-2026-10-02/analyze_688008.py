# -*- coding: utf-8 -*-
"""
Descriptive statistics for 688008.SH (澜起科技 / Montage Technology) computed
from the forward-adjusted daily K-line history downloaded via hithink-finance.

Inputs : raw/history_daily.json            (adjust=forward, remote, 3y window)
         raw/history_daily_unadjusted.json (adjust=none,    remote, 3y window)
Outputs: raw/analysis_688008.json  + stdout report

Standard library only (no pip install).
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import statistics as st

BASE = r"D:\quant\mira\analysis\688008-2026-10-02"
RAW = os.path.join(BASE, "raw")
HIST_FWD = os.path.join(RAW, "history_daily.json")
HIST_RAW = os.path.join(RAW, "history_daily_unadjusted.json")
OUT = os.path.join(RAW, "analysis_688008.json")
TZ = dt.timezone(dt.timedelta(hours=8))
ANN = 252  # trading days per year


def d(ms: int) -> dt.date:
    return dt.datetime.fromtimestamp(ms / 1000, TZ).date()


def pct(x: float) -> float:
    return round(x * 100.0, 4)


def months_back(anchor: dt.date, months: int) -> dt.date:
    y, m = anchor.year, anchor.month - months
    while m <= 0:
        m += 12
        y -= 1
    dim = [31, 29 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 28,
           31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return dt.date(y, m, min(anchor.day, dim))


def load(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        env = json.load(fh)
    item = env["data"]["item"]
    return sorted(
        ({"date": d(r["date_ms"]), "open": r["open_price"], "high": r["high_price"],
          "low": r["low_price"], "close": r["close_price"], "volume": r["volume"],
          "turnover": r["turnover"]} for r in item),
        key=lambda x: x["date"],
    )


fwd = load(HIST_FWD)
raw = load(HIST_RAW)
dates = [r["date"] for r in fwd]
closes = [r["close"] for r in fwd]
raw_closes = [r["close"] for r in raw]
first_d, last_d, last_close = dates[0], dates[-1], closes[-1]
n = len(fwd)

res: dict = {
    "thscode": "688008.SH",
    "adjust_basis": "forward (前复权) — dividend-inclusive; verified: raw close 2023-10-09 = 50.61, "
                    "forward = 49.33, cumulative dividends after that date = 0.30+0.39+0.20+0.39 = 1.28",
    "source_files": {"forward_adjusted": HIST_FWD, "unadjusted": HIST_RAW},
    "rows": n,
    "date_range": {"first": first_d.isoformat(), "last": last_d.isoformat()},
    "span_calendar_days": (last_d - first_d).days,
    "span_years_365_25": round((last_d - first_d).days / 365.25, 4),
    "last_close": last_close,
    "first_close_forward": closes[0],
    "first_close_raw": raw_closes[0],
    "coverage_note": ("the requested window opened 2023-10-01, a market holiday (National Day); "
                      "the previous trading day was 2023-09-28 and the first trading day of the "
                      "window is 2023-10-09, which is also the first bar returned — no missing data"),
}


def close_on_or_before(target: dt.date) -> tuple[dt.date, float] | None:
    pick = None
    for i, dd in enumerate(dates):
        if dd <= target:
            pick = i
        else:
            break
    return None if pick is None else (dates[pick], closes[pick])


# --------------------------------------------------- 1) total return windows
periods = [("1M", 1, "m"), ("3M", 3, "m"), ("6M", 6, "m"), ("1Y", 1, "y"), ("3Y", 3, "y")]
returns: dict = {}
for label, k, unit in periods:
    target = months_back(last_d, k) if unit == "m" else months_back(last_d, k * 12)
    hit = close_on_or_before(target)
    if hit is None or (unit == "y" and k == 3 and hit[0] != target):
        # 3Y target falls in the National Day holiday gap -> use the first bar
        ref_d, ref_c = first_d, closes[0]
        span_y = (last_d - ref_d).days / 365.25
        returns[label] = {
            "status": "first_available_bar_of_window",
            "target_date": target.isoformat(),
            "ref_date": ref_d.isoformat(),
            "ref_close": ref_c,
            "end_date": last_d.isoformat(),
            "end_close": last_close,
            "total_return_pct": pct(last_close / ref_c - 1.0),
            "actual_span_days": (last_d - ref_d).days,
            "actual_span_years": round(span_y, 4),
            "cagr_pct": pct((last_close / ref_c) ** (1.0 / span_y) - 1.0),
            "note": ("target date 2023-09-30 was a non-trading day inside the National Day holiday; "
                     "2023-10-09 is the first trading day of the 3Y window, so this is the full-window "
                     "return over 2.98 years rather than exactly 3.00 years"),
            "price_only_return_pct": pct(
                raw_closes[-1] / raw_closes[0] - 1.0),
        }
        continue
    ref_d, ref_c = hit
    span_y = (last_d - ref_d).days / 365.25
    idx = dates.index(ref_d)
    returns[label] = {
        "status": "exact_trading_day" if ref_d == target else "nearest_prior_trading_day",
        "target_date": target.isoformat(),
        "ref_date": ref_d.isoformat(),
        "ref_close": ref_c,
        "end_date": last_d.isoformat(),
        "end_close": last_close,
        "total_return_pct": pct(last_close / ref_c - 1.0),
        "actual_span_days": (last_d - ref_d).days,
        "actual_span_years": round(span_y, 4),
        "cagr_pct": pct((last_close / ref_c) ** (1.0 / span_y) - 1.0) if span_y > 1 else None,
        "price_only_return_pct": pct(raw_closes[-1] / raw_closes[idx] - 1.0),
    }
res["total_returns"] = returns

# ------------------------------------------- anchor sensitivity for the 1M arm
sens = {}
for label, iso in [("2026-08-31 (month-end variant)", "2026-08-31"),
                   ("2026-09-02 (exact 1 calendar month)", "2026-09-02"),
                   ("2026-08-28 (nearest prior bar used above)", "2026-08-28"),
                   ("2026-09-30-1M target 2026-08-30 -> prior bar", "2026-08-28")]:
    hit = close_on_or_before(dt.date.fromisoformat(iso[:10]))
    if hit:
        sens[label] = {"ref_date": hit[0].isoformat(), "ref_close": hit[1],
                       "total_return_pct": pct(last_close / hit[1] - 1.0)}
res["return_anchor_sensitivity_1m"] = {
    "why": "the 1M window has no exact trading-day anchor, and this stock moved sharply in "
           "early/mid September 2026, so the reported 1M figure is anchor-dependent",
    "alternatives": sens,
}

# --------------------------------------------------------- 2) max drawdown
cur_peak, cur_peak_d = -math.inf, None
mdd, mdd_peak_d, mdd_trough_d = 0.0, None, None
for dd, c in zip(dates, closes):
    if c > cur_peak:
        cur_peak, cur_peak_d = c, dd
    depth = 1.0 - c / cur_peak
    if depth > mdd:
        mdd, mdd_peak_d, mdd_trough_d = depth, cur_peak_d, dd
cd = dict(zip(dates, closes))
res["max_drawdown_3y"] = {
    "definition": "max_t (1 - close_t / running_max_close_up_to_t), closing prices, "
                  "full available 3Y window",
    "window": {"from": first_d.isoformat(), "to": last_d.isoformat()},
    "max_drawdown_pct": pct(-mdd),
    "peak_date": mdd_peak_d.isoformat(), "peak_close": cd[mdd_peak_d],
    "trough_date": mdd_trough_d.isoformat(), "trough_close": cd[mdd_trough_d],
    "recovery_days_peak_to_trough": (mdd_trough_d - mdd_peak_d).days,
    "last_close_vs_running_peak_pct": pct(last_close / max(closes) - 1.0),
    "running_peak_close": max(closes),
}

# ------------------------------------------------- 3) percentile vs 3Y range
lo, hi = min(closes), max(closes)
res["range_position_3y"] = {
    "window": {"from": first_d.isoformat(), "to": last_d.isoformat()},
    "min_close": lo, "min_close_date": dates[closes.index(lo)].isoformat(),
    "max_close": hi, "max_close_date": dates[closes.index(hi)].isoformat(),
    "last_close": last_close,
    "position_in_min_max_range_pct": pct((last_close - lo) / (hi - lo)),
    "percentile_rank_of_last_close_pct": pct(sum(1 for c in closes if c <= last_close) / n),
    "n_observations": n,
    "drawdown_from_3y_high_pct": pct(last_close / hi - 1.0),
    "above_3y_low_pct": pct(last_close / lo - 1.0),
}

# ------------------------------------------------------- 3b) 52-week range
W52 = min(250, n)
w52 = fwd[-W52:]
h52 = max(r["high"] for r in w52)
l52 = min(r["low"] for r in w52)
h52d = next(r["date"] for r in w52 if r["high"] == h52)
l52d = next(r["date"] for r in w52 if r["low"] == l52)
res["range_52w"] = {
    "definition": f"last {W52} trading bars, intraday high/low",
    "from": w52[0]["date"].isoformat(), "to": w52[-1]["date"].isoformat(),
    "high": h52, "high_date": h52d.isoformat(),
    "low": l52, "low_date": l52d.isoformat(),
    "last_close": last_close,
    "position_in_52w_range_pct": pct((last_close - l52) / (h52 - l52)),
    "below_52w_high_pct": pct(last_close / h52 - 1.0),
    "above_52w_low_pct": pct(last_close / l52 - 1.0),
}

# ------------------------------------------------------- 4) moving averages
mas: dict = {}
for w in (20, 60, 120, 250):
    if n >= w:
        v = sum(closes[-w:]) / w
        mas[f"MA{w}"] = {"window": w, "as_of": last_d.isoformat(), "value": round(v, 4),
                         "last_close_minus_ma": round(last_close - v, 4),
                         "last_close_vs_ma_pct": pct(last_close / v - 1.0)}
    else:
        mas[f"MA{w}"] = {"window": w, "status": "insufficient_history", "rows_available": n}
res["moving_averages"] = mas

# ------------------------------------------------------------ 5) volatility
def vol(rets: list[float], kind: str) -> dict:
    sd = st.stdev(rets)
    return {"return_type": kind, "n_returns": len(rets),
            "daily_stdev_pct": pct(sd),
            "annualized_vol_pct": pct(sd * math.sqrt(ANN)),
            "annualization": f"daily stdev x sqrt({ANN})",
            "mean_daily_return_pct": pct(st.mean(rets)),
            "min_daily_return_pct": pct(min(rets)),
            "max_daily_return_pct": pct(max(rets))}


W = 250
win = closes[-(W + 1):]
log_r = [math.log(win[i + 1] / win[i]) for i in range(len(win) - 1)]
sim_r = [win[i + 1] / win[i] - 1.0 for i in range(len(win) - 1)]
res["volatility_250d"] = {
    "window": {"from": dates[-(W + 1)].isoformat(), "to": last_d.isoformat(),
               "definition": f"last {W} closes -> {len(log_r)} daily returns"},
    "log_returns_annualized_pct": vol(log_r, "log")["annualized_vol_pct"],
    "simple_returns_annualized_pct": vol(sim_r, "simple")["annualized_vol_pct"],
    "log_returns_detail": vol(log_r, "log"),
    "simple_returns_detail": vol(sim_r, "simple"),
    "parkinson_high_low_annualized_pct": pct(
        (sum(math.log(r["high"] / r["low"]) for r in fwd[-W:]) / W) * math.sqrt(ANN)),
}
# shorter-window vol for context
for w in (20, 60):
    rr = [math.log(closes[i + 1] / closes[i]) for i in range(n - w - 1, n - 1)]
    res["volatility_250d"][f"log_returns_annualized_{w}d_pct"] = vol(rr, "log")["annualized_vol_pct"]

# ------------------------------------------------------------ 6) extra facts
ytd = close_on_or_before(dt.date(last_d.year - 1, 12, 31))
extra = {
    "last_5_bars": [{"date": x["date"].isoformat(), "close": x["close"],
                     "volume": x["volume"], "turnover": x["turnover"]} for x in fwd[-5:]],
    "return_5d_pct": pct(last_close / closes[-6] - 1.0),
    "return_20d_pct": pct(last_close / closes[-21] - 1.0),
    "volume_last_bar": fwd[-1]["volume"],
    "turnover_last_bar_cny": fwd[-1]["turnover"],
    "avg_volume_20d": round(sum(r["volume"] for r in fwd[-20:]) / 20, 2),
    "avg_turnover_20d_cny": round(sum(r["turnover"] for r in fwd[-20:]) / 20, 2),
    "avg_volume_250d": round(sum(r["volume"] for r in fwd[-250:]) / 250, 2),
    "max_intraday_swing_250d_pct": pct(max(r["high"] / r["low"] - 1.0 for r in fwd[-250:])),
}
if ytd:
    extra["ytd_reference"] = {"date": ytd[0].isoformat(), "close": ytd[1],
                             "return_ytd_pct": pct(last_close / ytd[1] - 1.0)}
res["recent_activity"] = extra

# --------------------------------------------------- 7) derived market cap
# valuation.snapshot returns only ratios; no market-cap endpoint exists in this CLI.
# Cross-check: market cap = PB_MRQ x equity attributable to owners (not directly available;
# holder_equity_total from financials.balance-sheet is used as an approximation).
pb = 11.476341
eq_total = 21437339800.75          # 2026-06-30 holder_equity_total, CNY
res["derived_market_cap_crosscheck"] = {
    "status": "derived, not a reported field - hithink-finance exposes no market-cap endpoint",
    "method": "market_cap ~= PB_MRQ x holder_equity_total(2026-06-30); shares ~= market_cap / last_close",
    "pb_mrq_used": pb,
    "holder_equity_total_cny_2026_06_30": eq_total,
    "implied_market_cap_cny": round(pb * eq_total, 2),
    "implied_market_cap_cny_100m": round(pb * eq_total / 1e8, 2),
    "implied_total_shares": round(pb * eq_total / last_close, 0),
    "caveats": ["book equity includes minority interests while PB normally uses owners' equity only, "
                "so the implied market cap is an upper-bound approximation",
                "rounded PB (6 dp) propagates ~0.01% error",
                "float market cap cannot be derived from any returned field"],
}

with open(OUT, "w", encoding="utf-8") as fh:
    json.dump(res, fh, ensure_ascii=False, indent=2)

# ------------------------------------------------------------------- report
LINES: list[str] = []


def out(s: str = "") -> None:
    LINES.append(s)
    print(s)


out(f"rows={n}  {first_d} .. {last_d}   last_close={last_close} (forward-adjusted)")
out("\n== TOTAL RETURN (forward-adjusted / dividend-inclusive) ==")
out(f"{'per':<5}{'ref_date':<12}{'ref_close':>11}{'end_close':>11}{'total%':>10}{'price_only%':>13}  status")
for label, _, _ in periods:
    r = returns[label]
    out(f"{label:<5}{r['ref_date']:<12}{r['ref_close']:>11.2f}{r['end_close']:>11.2f}"
        f"{r['total_return_pct']:>10.2f}{r['price_only_return_pct']:>13.2f}  {r['status']}")
out("\n== 1M ANCHOR SENSITIVITY ==")
out(json.dumps(res["return_anchor_sensitivity_1m"], ensure_ascii=False, indent=1))
out("\n== MAX DRAWDOWN ==")
out(json.dumps(res["max_drawdown_3y"], ensure_ascii=False, indent=1))
out("\n== RANGE POSITION ==")
out(json.dumps(res["range_position_3y"], ensure_ascii=False, indent=1))
out("\n== 52-WEEK RANGE ==")
out(json.dumps(res["range_52w"], ensure_ascii=False, indent=1))
out("\n== MOVING AVERAGES ==")
out(json.dumps(mas, ensure_ascii=False, indent=1))
out("\n== VOLATILITY 250d ==")
out(json.dumps(res["volatility_250d"], ensure_ascii=False, indent=1))
out("\n== RECENT ACTIVITY ==")
out(json.dumps(extra, ensure_ascii=False, indent=1))
out("\n== DERIVED MARKET CAP CROSS-CHECK ==")
out(json.dumps(res["derived_market_cap_crosscheck"], ensure_ascii=False, indent=1))
out(f"\nwrote {OUT}")

with open(os.path.join(RAW, "analysis_report.txt"), "w", encoding="utf-8") as fh:
    fh.write("\n".join(LINES) + "\n")
