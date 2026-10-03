"""Consolidated fundamental metric pack for 688008.SH (Montage Technology).

Sources: D:\\quant\\mira\\analysis\\688008-2026-10-02\\raw\\*.json pulled from
hithink-finance CLI (remote THS financial data service), retrieved 2026-10-02.
"""
import json
import os

RAW = r"D:\quant\mira\analysis\688008-2026-10-02\raw"
SHARES = 1134.8e6  # inferred: 2025 parent NP 22.3557亿 / EPS 1.97


def items(name):
    with open(os.path.join(RAW, name), "r", encoding="utf-8") as f:
        return json.load(f)["data"]["item"]


inc_a = {r["fiscal_year"]: r for r in items("income_annual.json")}
inc_q = {(str(r["fiscal_year"]), r["fiscal_period"]): r for r in items("income_quarterly.json")}
bs_a = {r["fiscal_year"]: r for r in items("bs_annual.json")}
bs_q = {(str(r["fiscal_year"]), r["fiscal_period"]): r for r in items("bs_quarterly.json")}
cf_a = {r["fiscal_year"]: r for r in items("cf_annual.json")}
cf_q = {(str(r["fiscal_year"]), r["fiscal_period"]): r for r in items("cf_quarterly.json")}

PX = 202.31
MKTCAP = PX * SHARES / 1e8

print("=" * 104)
print("A. ANNUAL P&L, MARGIN AND CASH TREND (亿元 unless noted)")
print("=" * 104)
print(f"{'FY':<6}{'Rev':>9}{'YoY%':>8}{'GM%':>7}{'R&D':>8}{'R&D%':>7}{'Mgmt':>8}{'OpProfit':>10}"
      f"{'OpM%':>7}{'NP':>8}{'Minority':>10}{'NetM%':>7}{'EPS':>7}")
prev = None
for y in sorted(inc_a):
    r = inc_a[y]
    rev, cos = r["operating_income"] / 1e8, r["operating_costs"] / 1e8
    rd, mg = r["research_and_development_expenses"] / 1e8, r["manage_fee"] / 1e8
    op, np_, par = r["operating_profit"] / 1e8, r["net_profit"] / 1e8, r["parent_holder_net_profit"] / 1e8
    yoy = f"{(rev/prev-1)*100:7.1f}" if prev else f"{'n/a':>7}"
    print(f"{y:<6}{rev:>9.2f}{yoy:>8}{100*(rev-cos)/rev:>7.1f}{rd:>8.2f}{100*rd/rev:>7.1f}{mg:>8.2f}"
          f"{op:>10.2f}{100*op/rev:>7.1f}{np_:>8.2f}{np_-par:>10.2f}{100*par/rev:>7.1f}{r['basic_eps']:>7.2f}")
    prev = rev
print()

print("=" * 104)
print("B. SINGLE-QUARTER P&L (亿元)")
print("=" * 104)
rows = []
for (y, q) in sorted(inc_q, key=lambda k: (k[0], k[1])):
    r = inc_q[(y, q)]
    if q == "Q1":
        v = dict(rev=r["operating_income"], cos=r["operating_costs"], rd=r["research_and_development_expenses"],
                 op=r["operating_profit"], par=r["parent_holder_net_profit"], eps=r["basic_eps"])
    else:
        p = inc_q.get((y, f"Q{int(q[1])-1}"))
        if p is None:
            continue
        v = {k: r[f] - p[f] for k, f in [("rev", "operating_income"), ("cos", "operating_costs"),
             ("rd", "research_and_development_expenses"), ("op", "operating_profit"),
             ("par", "parent_holder_net_profit"), ("eps", "basic_eps")]}
    rows.append((f"{y}{q}", v))

print(f"{'Qtr':<9}{'Rev':>9}{'RevYoY%':>10}{'QoQ%':>8}{'GM%':>7}{'R&D':>8}{'R&D%':>7}{'OpProfit':>10}{'OpM%':>7}{'NP':>8}{'NetM%':>7}{'EPS':>7}")
for i, (label, v) in enumerate(rows):
    rev = v["rev"] / 1e8
    yoy = f"{(rev/ (rows[i-4][1]['rev']/1e8) -1)*100:9.1f}" if i >= 4 else f"{'n/a':>9}"
    qoq = f"{(rev/ (rows[i-1][1]['rev']/1e8) -1)*100:7.1f}" if i >= 1 else f"{'n/a':>7}"
    print(f"{label:<9}{rev:>9.2f}{yoy:>10}{qoq:>8}{100*(rev-v['cos']/1e8)/rev:>7.1f}"
          f"{v['rd']/1e8:>8.2f}{100*v['rd']/v['rev']:>7.1f}{v['op']/1e8:>10.2f}"
          f"{100*v['op']/v['rev']:>7.1f}{v['par']/1e8:>8.2f}{100*v['par']/v['rev']:>7.1f}{v['eps']:>7.2f}")
print()

print("=" * 104)
print("C. BALANCE SHEET AND RETURNS (亿元)")
print("=" * 104)
print(f"{'Period':<9}{'Assets':>9}{'Cash':>9}{'AR':>8}{'Liab':>8}{'Equity':>9}{'L/A%':>7}"
      f"{'NetCash':>9}{'ROE_ann%':>10}")
for (y, q) in sorted(bs_q, key=lambda k: (k[0], k[1])):
    b = bs_q[(y, q)]
    a, c, ar, l, e = (b["assets_total"]/1e8, b["cash"]/1e8, b["accounts_receivable"]/1e8,
                      b["total_debt"]/1e8, b["holder_equity_total"]/1e8)
    par = inc_q[(y, q)]["parent_holder_net_profit"] / 1e8
    roe = 100 * par / e * (4 if q == "Q1" else 2 if q == "Q2" else 4/3 if q == "Q3" else 1)
    print(f"{str(y)+q:<9}{a:>9.2f}{c:>9.2f}{ar:>8.2f}{l:>8.2f}{e:>9.2f}{100*l/a:>7.2f}{c-l:>9.2f}{roe:>10.1f}")
print()

print("=" * 104)
print("D. CASH FLOW AND QUALITY (亿元)")
print("=" * 104)
print(f"{'FY':<6}{'OpCF':>9}{'Capex':>8}{'FCF':>9}{'InvCF':>9}{'FinCF':>9}{'NP':>8}{'OpCF/NP%':>10}{'FCF/NP%':>9}")
for y in sorted(cf_a):
    c = cf_a[y]
    par = inc_a[y]["parent_holder_net_profit"] / 1e8
    opcf, capex = c["act_cash_flow_net"]/1e8, (c["pay_fixed_assets_etc_cash"] or 0)/1e8
    print(f"{y:<6}{opcf:>9.2f}{capex:>8.2f}{opcf-capex:>9.2f}{c['invest_cash_flow_net']/1e8:>9.2f}"
          f"{(c['financing_cash_flow_net'] or 0)/1e8:>9.2f}{par:>8.2f}"
          f"{100*opcf/par:>10.1f}{100*(opcf-capex)/par:>9.1f}")
print()

print("=" * 104)
print("E. VALUATION GRID at 2026-09-30 close of 202.31 (shares 11.348亿, mkt cap "
      f"{MKTCAP:.1f}亿元)")
print("=" * 104)
ttm_par = (inc_q[("2026", "Q2")]["parent_holder_net_profit"]
           - inc_q[("2025", "Q2")]["parent_holder_net_profit"]
           + inc_a[2025]["parent_holder_net_profit"]) / 1e8
h1_26 = inc_q[("2026", "Q2")]["parent_holder_net_profit"] / 1e8
ttm_rev = (inc_q[("2026", "Q2")]["operating_income"] - inc_q[("2025", "Q2")]["operating_income"]
           + inc_a[2025]["operating_income"]) / 1e8
eq = bs_q[("2026", "Q2")]["holder_equity_total"] / 1e8
cash = bs_q[("2026", "Q2")]["cash"] / 1e8
liab = bs_q[("2026", "Q2")]["total_debt"] / 1e8

print(f"TTM revenue           = {ttm_rev:8.2f} 亿元")
print(f"TTM parent NP         = {ttm_par:8.2f} 亿元   (= FY25 22.36 + H1'26 19.97 - H1'25 11.59)")
print(f"book equity (2026H1)  = {eq:8.2f} 亿元")
print()
print(f"{'multiple':<34}{'value':>12}")
print(f"{'P/E  on TTM parent NP':<34}{MKTCAP/ttm_par:>12.1f}")
print(f"{'P/E  on H1 2026 annualised (x2)':<34}{MKTCAP/(h1_26*2):>12.1f}")
print(f"{'P/E  on FY2025':<34}{MKTCAP/22.3557:>12.1f}")
print(f"{'P/S  on TTM revenue':<34}{MKTCAP/ttm_rev:>12.1f}")
print(f"{'P/B  on 2026H1 equity':<34}{MKTCAP/eq:>12.1f}")
print(f"{'EV   = mktcap - net cash':<34}{MKTCAP-(cash-liab):>12.1f}")
print(f"{'EV / TTM parent NP':<34}{(MKTCAP-(cash-liab))/ttm_par:>12.1f}")
print(f"{'EV / H1 ann. parent NP':<34}{(MKTCAP-(cash-liab))/(h1_26*2):>12.1f}")
print(f"{'EV / TTM revenue':<34}{(MKTCAP-(cash-liab))/ttm_rev:>12.1f}")
print(f"{'FCF yield on FY2025 FCF':<34}{100*(20.22-2.66)/MKTCAP:>11.1f}%")
print(f"{'Dividend yield (FY25 DPS .39+.20)':<34}{100*0.59/PX:>11.2f}%")
print()

print("=" * 104)
print("F. IMPLIED EXPECTATIONS — what growth does the current price require?")
print("=" * 104)
print("Reverse-DCF style: assume 10% discount rate, solve for the flat 10-year FCF growth")
print("needed to justify an EV of %.1f亿元, then the terminal fade." % (MKTCAP-(cash-liab)))
ev = MKTCAP - (cash - liab)
fcf0 = 17.56  # FY2025 FCF in 亿元
r = 0.10
for g in [0.00, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35]:
    pv = sum(fcf0 * (1 + g) ** t / (1 + r) ** t for t in range(1, 11))
    # terminal at 3% growth on year-10 FCF
    tv = fcf0 * (1 + g) ** 10 * 1.03 / (r - 0.03)
    pv += tv / (1 + r) ** 10
    print(f"  flat 10y FCF growth {g*100:5.1f}%  ->  DCF value = {pv:9.1f} 亿元  "
          f"vs EV {ev:.1f}  ({pv/ev-1:+.0%})")
print()
print("Same grid but using H1'26 annualised run-rate free cash flow proxy:")
fcf_run = (13.28 - 1.03) * 2  # H1'26 OpCF - Q1 capex, annualised
print(f"  annualised H1'26 FCF proxy = {fcf_run:.2f} 亿元")
for g in [0.00, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35]:
    pv = sum(fcf_run * (1 + g) ** t / (1 + r) ** t for t in range(1, 11))
    tv = fcf_run * (1 + g) ** 10 * 1.03 / (r - 0.03)
    pv += tv / (1 + r) ** 10
    print(f"  flat 10y FCF growth {g*100:5.1f}%  ->  DCF value = {pv:9.1f} 亿元  "
          f"vs EV {ev:.1f}  ({pv/ev-1:+.0%})")
