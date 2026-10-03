"""FINAL consolidated metrics for 688008.SH using primary-source figures.

Share count: 1,222,200,021 (after H-share greenshoe exercised; per 2026-02-10
announcement). 2026-06-30 total 1,220,538,021 with 11,781,000 treasury shares.
Primary sources: 2026 H1 report, FY2025 业绩快报, H-share listing announcement.
"""
import json
import os

RAW = r"D:\quant\mira\analysis\688008-2026-10-02\raw"


def items(name):
    with open(os.path.join(RAW, name), "r", encoding="utf-8") as f:
        return json.load(f)["data"]["item"]


inc_a = {r["fiscal_year"]: r for r in items("income_annual.json")}
inc_q = {(str(r["fiscal_year"]), r["fiscal_period"]): r for r in items("income_quarterly.json")}
bs_q = {(str(r["fiscal_year"]), r["fiscal_period"]): r for r in items("bs_quarterly.json")}
cf_q = {(str(r["fiscal_year"]), r["fiscal_period"]): r for r in items("cash-flow-quarterly.json")} \
    if os.path.exists(os.path.join(RAW, "cash-flow-quarterly.json")) else \
    {(str(r["fiscal_year"]), r["fiscal_period"]): r for r in items("cf_quarterly.json")}

PX = 202.31
SHARES = 1_222_200_021            # total share capital after greenshoe
TREASURY = 11_781_000             # buyback account at 2026-08-28
EFF_SHARES = SHARES - TREASURY
MKTCAP = PX * SHARES / 1e8

# ---- primary-source figures (亿元) ----
H1_REV, H1_PAR, H1_DEDUCT = 33.3549752181, 19.9737451787, 13.2247853633
H1_NONREC = 6.7489598154          # 非经常性损益合计
H1_SBC_NET = 1.8267221771         # 股份支付影响 (税后)
H1_FX_LOSS = 1.74                 # 汇兑损失
H1_INVEST_FV = 6.82               # 投资收益+公允价值变动
H1_GROSSPROFIT = 21.79
H1_SEG_INTERCONNECT_REV, H1_SEG_INTERCONNECT_GM = 31.1129190174, 0.693
H1_SEG_JINDAI_REV, H1_SEG_JINDAI_COST = 2.2001363150, 1.9872026227
FY25_REV, FY25_PAR, FY25_DEDUCT = 54.5631678363, 22.3556997018, 20.216287  # 扣非 from 快报
FY25_SBC_NET = 4.12
FY25_EX_SBC = 26.47
FY25_EX_SBC_DEDUCT = 24.33
H1_25_PAR, H1_25_DEDUCT = 11.5907037972, 10.9138575870

print("=" * 100)
print("A. MARKET CAP AND PER-SHARE (corrected)")
print("=" * 100)
print(f"Total share capital (after greenshoe)  = {SHARES:,} shares")
print(f"Treasury shares (buyback acct)         = {TREASURY:,}")
print(f"Close 2026-09-30                       = {PX} CNY")
print(f"TOTAL MARKET CAP                       = {MKTCAP:,.1f} 亿元   (previously mis-stated as 2295.8)")
print(f"Implied free-float-ish shares          = {EFF_SHARES:,}")
print(f"Book value per share (2026H1 parent)   = {215.1618256585/SHARES*1e8:.2f} CNY")
print(f"Cross-check PB: mktcap / parent equity  = {MKTCAP/(215.1618256585/1e8):.2f}x  vs served PB_MRQ 11.476")
print(f"Cross-check PE_MRQ: mktcap / (H1 PAR*2) = {MKTCAP/(H1_PAR*2):.2f}x  vs served PE_MRQ 61.813")
print()

# ---- TTM ----
ttm_rev = inc_q[("2026", "Q2")]["operating_income"] / 1e8 - inc_q[("2025", "Q2")]["operating_income"] / 1e8 + FY25_REV
ttm_par = inc_q[("2026", "Q2")]["parent_holder_net_profit"] / 1e8 - inc_q[("2025", "Q2")]["parent_holder_net_profit"] / 1e8 + FY25_PAR
ttm_deduct = H1_DEDUCT - H1_25_DEDUCT + FY25_DEDUCT

eq = bs_q[("2026", "Q2")]["holder_equity_total"] / 1e8
parent_eq = 215.1618256585 / 1e8
cash = bs_q[("2026", "Q2")]["cash"] / 1e8
liab = bs_q[("2026", "Q2")]["total_debt"] / 1e8
net_cash = cash - liab

print("=" * 100)
print("B. VALUATION GRID — reported vs CORE earnings")
print("=" * 100)
print(f"TTM revenue                       = {ttm_rev:8.2f} 亿元")
print(f"TTM 归母 (reported)               = {ttm_par:8.2f} 亿元")
print(f"TTM 扣非归母 (core)               = {ttm_deduct:8.2f} 亿元   <- strip non-recurring")
print(f"TTM 扣非+剔除股份支付 (core-core) = {ttm_deduct + FY25_SBC_NET*0.5 + H1_SBC_NET:8.2f} 亿元")
print(f"2026H1 年化 归母                  = {H1_PAR*2:8.2f} 亿元")
print(f"2026H1 年化 扣非                  = {H1_DEDUCT*2:8.2f} 亿元")
print(f"Net cash (cash {cash:.2f} - liabilities {liab:.2f}) = {net_cash:.2f} 亿元")
print(f"EV = mktcap - net cash            = {MKTCAP-net_cash:8.2f} 亿元")
print()
hdr = f"{'multiple':<42}{'value':>12}"
print(hdr); print("-" * len(hdr))
rows = [
    ("P/E  TTM reported 归母", MKTCAP / ttm_par),
    ("P/E  TTM 扣非 (core earnings)", MKTCAP / ttm_deduct),
    ("P/E  2026H1 annualised reported", MKTCAP / (H1_PAR * 2)),
    ("P/E  2026H1 annualised 扣非", MKTCAP / (H1_DEDUCT * 2)),
    ("P/E  FY2025 reported", MKTCAP / FY25_PAR),
    ("P/E  FY2025 扣非", MKTCAP / FY25_DEDUCT),
    ("P/E  consensus 2026E 35.22亿 (THS, 17 inst.)", MKTCAP / 35.22),
    ("P/E  consensus 2027E 46.77亿", MKTCAP / 46.77),
    ("P/E  consensus 2028E 62.38亿", MKTCAP / 62.38),
    ("P/S  TTM revenue", MKTCAP / ttm_rev),
    ("P/B  on 2026H1 parent equity", MKTCAP / parent_eq),
    ("EV / TTM reported 归母", (MKTCAP - net_cash) / ttm_par),
    ("EV / TTM 扣非 (core)", (MKTCAP - net_cash) / ttm_deduct),
    ("EV / consensus 2027E 归母", (MKTCAP - net_cash) / 46.77),
    ("EV / TTM revenue", (MKTCAP - net_cash) / ttm_rev),
]
for label, v in rows:
    print(f"{label:<42}{v:>12.1f}")
print()
print(f"EV / FY2025 FCF (17.56亿)         = {(MKTCAP-net_cash)/17.56:12.1f}")
print(f"EV / 2026H1 annualised FCF (21.46) = {(MKTCAP-net_cash)/21.46:12.1f}")
print(f"Reported 归母 margin TTM          = {100*ttm_par/ttm_rev:12.1f}%")
print(f"CORE (扣非) margin TTM            = {100*ttm_deduct/ttm_rev:12.1f}%")
print()

print("=" * 100)
print("C. H1 2026 PROFIT BRIDGE — why reported +72.3% but core only +21.2%")
print("=" * 100)
print(f"归母净利 2025H1                   = {H1_25_PAR:8.2f}")
print(f"归母净利 2026H1                   = {H1_PAR:8.2f}   (+{100*(H1_PAR/H1_25_PAR-1):.1f}%)")
print(f"  其中 非经常性损益 2026H1        = {H1_NONREC:8.2f}   ({100*H1_NONREC/H1_PAR:.0f}% of net profit)")
print(f"        - 金融资产公允价值变动及处置 {6.8727191015:8.2f}")
print(f"        - 政府补助                  {0.0575488129:8.2f}")
print(f"        - 其他/结构性存款            {0.0598757:8.2f}")
print(f"        - 所得税及少数股东影响       {-0.2367343169-0.0035351104:8.2f}")
print(f"  非经常性损益 2025H1              = {H1_25_PAR-H1_25_DEDUCT:8.2f}")
print(f"  => 扣非归母 2026H1               = {H1_DEDUCT:8.2f}   (+{100*(H1_DEDUCT/H1_25_DEDUCT-1):.1f}%)")
print()
print("Cost lines that depressed reported profit:")
print(f"  汇兑损失 (H1 2026)               = {H1_FX_LOSS:8.2f}   vs 0.07 in H1 2025  ->  -{H1_FX_LOSS-0.07:.2f} yoy drag")
print(f"  股份支付费用 (计入经常性损益)     = {1.91:8.2f}   (税后影响 {H1_SBC_NET:.2f})")
print(f"  剔除股份支付后 扣非归母           = {15.05:8.2f}   (+19.0%)")
print()

print("=" * 100)
print("D. SEGMENT ECONOMICS (H1 2026, primary source)")
print("=" * 100)
seg_rows = [
    ("互连类芯片", H1_SEG_INTERCONNECT_REV, H1_SEG_INTERCONNECT_REV * (1 - H1_SEG_INTERCONNECT_GM)),
    ("津逮产品", H1_SEG_JINDAI_REV, H1_SEG_JINDAI_COST),
    ("租赁", 0.0419198857, 0.0291165970),
]
tot_rev = sum(r[1] for r in seg_rows)
tot_gp = sum(r[1] - r[2] for r in seg_rows)
print(f"{'segment':<14}{'revenue':>10}{'cost':>10}{'gross profit':>14}{'GM%':>8}{'% of rev':>10}{'% of GP':>9}")
for name, rev, cost in seg_rows:
    gp = rev - cost
    print(f"{name:<14}{rev:>10.2f}{cost:>10.2f}{gp:>14.2f}{100*gp/rev:>8.1f}"
          f"{100*rev/tot_rev:>10.1f}{100*gp/tot_gp:>9.1f}")
print(f"{'TOTAL':<14}{tot_rev:>10.2f}{sum(r[2] for r in seg_rows):>10.2f}{tot_gp:>14.2f}"
      f"{100*tot_gp/tot_rev:>8.1f}{100.0:>10.1f}{100.0:>9.1f}")
print()
print("Same split by geography:")
geo = [("境内(含香港)", 10.1141482008, 4.7238562889), ("境外", 23.1989071316, 6.8169691410)]
for name, rev, cost in geo:
    print(f"  {name:<12} revenue {rev:7.2f}  GM {100*(rev-cost)/rev:5.1f}%  "
          f"({100*rev/(10.1141482008+23.1989071316):.1f}% of revenue)")
print()
print("KEY INSIGHT: 津逮 = 6.6% of revenue but only 0.6% of gross profit.")
print(f"  津逮 GM = {100*(H1_SEG_JINDAI_REV-H1_SEG_JINDAI_COST)/H1_SEG_JINDAI_REV:.1f}%  vs 互连 {100*H1_SEG_INTERCONNECT_GM:.1f}%")
q1_jd, q2_jd = 0.42, H1_SEG_JINDAI_REV - 0.42
print(f"  津逮 single-quarter revenue: Q1 {q1_jd:.2f} -> Q2 {q2_jd:.2f} (+{100*(q2_jd/q1_jd-1):.0f}% qoq)")
print("  => the Q1->Q2 total gross-margin fall (69.8% -> 61.8%) is a MIX effect, not pricing.")
print()

print("=" * 100)
print("E. IMPLIED EXPECTATIONS — reverse DCF on CORRECTED EV")
print("=" * 100)
ev = MKTCAP - net_cash
r = 0.10
for label, fcf0 in [("FY2025 FCF 17.56亿", 17.56), ("2026H1 annualised FCF 21.46亿", 21.46)]:
    print(f"Base: {label}   EV = {ev:.1f}亿元")
    for g in [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40]:
        pv = sum(fcf0 * (1 + g) ** t / (1 + r) ** t for t in range(1, 11))
        pv += fcf0 * (1 + g) ** 10 * 1.03 / (r - 0.03) / (1 + r) ** 10
        print(f"   flat 10y FCF growth {g*100:5.1f}%  ->  {pv:9.1f} 亿元  ({pv/ev-1:+6.0%} vs EV)")
    print()
print("Historical delivery for comparison:")
print("  revenue CAGR 2016-2025 (8.45 -> 54.56亿)      = 23.0%")
print("  FCF CAGR 2016-2025 (3.82 -> 17.56亿)          = 18.5%")
print("  revenue CAGR 2019-2025 (17.38 -> 54.56亿)     = 21.0%")
print(f"  consensus implied 2025-2028 归母 CAGR         = {100*((62.38/22.36)**(1/3)-1):.1f}%")

