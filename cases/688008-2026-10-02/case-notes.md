# Case Notes — 澜起科技 (688008.SH) | first-pass deep_dive, 2026-10-02

## What this case was

User prompt: `mira 深度分析一下澜起科技`. Routed to `loops/research-loop.md` at `depth_mode = deep_dive`
(single equity, first pass). No prior object state existed for 688008.

## How the evidence was actually obtained (and what broke)

This matters because it shaped the confidence profile of every conclusion.

1. **`web_search` was broken at the harness level** — HTTP 404, endpoint misconfigured to
   `https://api.tavily.com/messages`. Every search returned an API error, not results.
2. **`web_fetch` was broken** — every hostname resolves to `198.18.0.66`, a fake-IP range from a
   local Clash proxy (127.0.0.1:7897); the SSRF guard rejects non-public IPs.
3. **Recovery path that worked**: the `mira_data` toolkit ships a CNINFO (巨潮资讯网) disclosure
   adapter that hits the official announcement API directly. Its `probe()` succeeded, so the whole
   evidence base pivoted to (a) CNINFO primary filings and (b) the authenticated `hithink-finance`
   CLI.
4. Added `pypdf` into a **project-local `_pylibs` directory** (`pip install --target`) rather than
   touching the system Python, then extracted text from 10 filings including the full 248-page
   FY2025 annual report and 196-page H1 2026 report.

**Net effect**: the case ended up with *better* primary-source depth than a typical web-search run,
but with **zero independent third-party industry coverage**. That trade-off is stated in the memo.

## Why the analysis changed course mid-flight

Three findings forced rewrites. Each is worth remembering as a pattern.

### 1. The share count was wrong, which moved market cap by 7.7%

I first inferred shares as `FY2025 parent NP ÷ basic EPS` = 1,134.8m. That gave a market cap of
2,295.8亿元. A parallel subagent independently derived ~1,216m shares from a PB cross-check, which
didn't match. Reading the actual H-share listing announcement settled it: **post-greenshoe share
capital is 1,222,200,021**. Corrected market cap = 2,472.6亿元.

**Pattern**: EPS-implied share counts silently break whenever a company does a mid-period equity
raise, because basic EPS uses a *time-weighted* average. Always prefer the filing's stated capital.

### 2. The parallel news stream was confidently wrong on a material risk

The news-research subagent asserted: *"NO convertible bonds, NO regulatory inquiry/litigation/
penalty, NO management departures in 2026."*

The CNINFO announcement index contained, dated 2026-07-17: **《关于配合韩国相关调查的说明公告》** —
the Seoul Central District Prosecutors' Office (Fair Trade Investigation Division) conducted an
on-site search and evidence seizure at Montage's Korea office on 2026-07-15 over potential antitrust
violations.

This is arguably the single most important fact for the equity, and it aligned with the price
action (two worst days, -17.0% and -13.1%, straddle the raid and its disclosure).

**Pattern**: a secondary summary asserting a *negative* ("no regulatory action") is unfalsifiable
from search results alone and is exactly the kind of claim that must be checked against the primary
disclosure index. Logged as `contradicted` (evidence-log P18) and excluded from the conclusion chain.

### 3. (added after the harness fix) The company's own disclosure was accurate but materially incomplete on scope

With `web_search` repaired, the first query returned Yonhap's wire report:
**《검찰, '반도체 부품 담합' 몬타지 테크놀로지 등 3곳 압수수색》** — the same Seoul Central
District Prosecutors' Office division raided the Korean offices of **all three** global
memory-interface suppliers on the same day: Montage (China), Renesas (Japan) and Rambus (US),
on suspicion of **price-fixing** under the Fair Trade Act, and seized officials' mobile phones.
Corroborated independently in English by Seoul Economic Daily.

The company's own 公告 2026-040 named only its own Korea office and used the neutral phrase
「潜在的违反反垄断相关法规」. **Every sentence in it is true; the scope is simply not disclosed.**

**Why this matters more than a normal correction:** it reframes the risk. It is no longer
"did one company do something wrong" but "was a CR3 > 90% oligopoly coordinating". That
attacks the *source* of the 69.3% interconnect gross margin — through behavioural remedies and
customers (Samsung / SK hynix / Micron, i.e. all of global DRAM) diversifying supply for
compliance reasons — rather than merely producing a fine. It also supplies a competing
explanation for the one thing I had taken as proof of moat: high, rising margins that never
broke down even in the 2023 downturn.

**Pattern**: a company disclosure about an adverse event is the *floor* of what is known, not
the ceiling. Issuers control the framing and the scope; the wire services report what was
actually done. When an adverse event is material, read the trade press *before* concluding on
the filing alone — and never let a neutral company phrase ("potential violation of antitrust
regulations") substitute for the charge actually under investigation ("price-fixing").

**I did not let this flip the view to bullish.** "It's not just us" reads like a relief, but
the logic is asymmetric: if coordination is established, the excess-profit *source* is
invalidated. And the market already took ~30% off in the two sessions after the raid, so this
is a partially-priced risk, not a tradable mispricing.

### 4. The headline growth number was 3.4x the core growth number

Reported H1 2026 归母净利 +72.33%; 扣非 +21.17%. The 6.75亿元 gap is non-recurring and 6.87亿元 of
it is financial-asset fair-value/disposal gains — mostly the XConn/Marvell stake sale. The company's
own 非经常性损益 table made this unambiguous.

**Pattern**: when reported net margin (49.9% TTM) and 扣非 net margin (36.6% TTM) differ by 13 points,
any valuation built on the reported number is wrong by roughly the same order. This changed the
headline P/E from "57–80x" to "**93–110x on core earnings**".

## The finding that *removed* a scare rather than adding one

Q1→Q2 2026 total gross margin fell 69.79% → 61.83%, an 8pp drop that on its face looks like pricing
collapse. The segment note resolved it: 津逮® (server platform, 9.7% GM, 1.0% of gross profit) went
from 0.42亿元 to 1.78亿元 in the quarter (+324% qoq). Pure mix arithmetic. The interconnect line's own
GM was 67.4% in Q2, *up* 3.2pp yoy.

**Pattern**: a consolidated margin move must always be decomposed by segment before it is
interpreted as pricing power. This is cheap to check and expensive to get wrong.

## Judgment calls I made and could be argued with

- **Used a 10% discount rate** in the reverse DCF. At 8.5% the required growth falls from ~30% to
  ~25–26%, which would materially soften the conclusion. Stated as a judgment, not a fact.
- **Did not add back the 1.74亿元 FX loss** to core earnings, even though it is non-operating,
  because the RMB appreciation exposure is a *structural consequence* of holding H-share proceeds
  in foreign currency and will recur. Defensible either way.
- **Treated the Korean investigation as `irreducible_uncertainty`** rather than modelling scenarios.
  The company itself says timing and outcome are unpredictable. Rather than invent probabilities, I
  capped readiness at `watch_only`.
- **Did not reconcile** the pre-announcement's implied Q2 interconnect GM with the final report to
  the last decimal. The final filed report governs; the residual is logged as G05.

## Open items carried forward

| id | item | why it matters |
| --- | --- | --- |
| P17 | Korean antitrust outcome | only variable that can materially change the thesis |
| G06 | Full quarterly bridge from 营业利润 to 利润总额 | needed to split "operating +74.5%" from "FX -1.67亿" cleanly |
| G05 | Q2 single-quarter GM not cross-verified vs a company figure | derived by differencing cumulative YTD |
| G02/G03 | Inventory turnover 1.22x, net-profit cash content 66.5% | working-capital quality unresolved |
| G07 | No independent third-party industry source at all | MRDIMM timing & market share rest on company statements only |

## Environment fixes worth making

1. `web_search` endpoint is misconfigured (points at a Tavily *messages* URL). Per the harness docs
   this is user-settable at Settings > Plugins > Plugin configuration > Web search.
2. `web_fetch` cannot reach public hosts while a local proxy resolves them into `198.18.0.66`.
3. `hithink-finance` local-DB commands need read-write access to
   `C:\Users\lsl\AppData\Local\hithink-finance\data\market.duckdb`, which the earlier
   `workspace-write` policy denied. Workaround used: copy the DB into the case directory and pass
   `--db`. Worth whitelisting if local panel work is expected regularly.

## Deliverables in this case directory

- `investment-memo.md` — the analysis (routing card, facts, judgments, reverse DCF, refresh conditions)
- `evidence-log.csv` — 58 rows, canonical v1.2 schema + evidence-posture fields
- `case-notes.md` — this file
- `research-package-manifest.json` — machine index (`mira_research_package_v1`)
- `routing.json` — machine routing card; the human-readable basis is in the memo
- `final_metrics.py` / `metrics.py` / `tech.py` / `relative.py` — reproducible calculations
- `cninfo.py` / `fetch_pdfs.py` / `fetch_pdfs2.py` / `dump_titles.py` — evidence acquisition
- `raw/` — vendor JSON pulls + the CNINFO announcement index (**untracked**, gitignored)
- `pdftext/` — text of 10 primary filings incl. the full FY2025 annual report and H1 2026
  report (**untracked**, gitignored; re-derivable via `mira_data cninfo text`)

The row count in this file said 54 when the log actually held 58; corrected on 2026-10-03
during migration. The log was re-expressed from its original case-local claim list
(`claim_id` / `source_ref` columns) into the repository's canonical v1.2 columns so the
repo validator can check it. No claim, figure, date or judgment was altered in that pass.

## Post-hoc verification with the shareholder channel (2026-10-03)

This case was built on 2026-10-02, before `mira_data fetch shareholders_top10` /
`shareholders_free_float` existed. Re-reading the ownership structure through that channel
now reproduces this case's own §2.8 findings independently, which is a useful check on both:

| this case (§2.8, 2026-06-30) | channel (2026-07-23, the later filed period) |
| --- | --- |
| WLT Partners 3.68% | 3.6879% |
| 上海融迎 2.10% | 2.1038% |
| 陆股通 / 香港中央结算 11.93% | 11.43% |
| "前十大股东里有 5 只被动指数 ETF" | 4 STAR50 / chip ETFs visible by name, plus the buyback account |
| "无控股股东，无实际控制人" | no holder above 11.5%; largest is the HK clearing nominee |

The 陆股通 move 11.93% → 11.43% is a change between two filed periods, not a discrepancy.
Note the channel's denominator: the 3.68% and 2.10% figures in §2.8 come from the
**流通股** basis, so they reconcile with `shareholders_free_float`, not with the
占总股本 table — which is exactly why the two tables are published separately and labelled.

One thing this channel adds that §2.8 did not state explicitly: for a dual-listed issuer the
top-ten table mixes share classes — `香港中央结算有限公司` 11.43% is the northbound holding of
**流通A股**, while `HKSCC NOMINEES LIMITED` 6.21% is the **流通H股** depositary line. Both
appear in the same list, so any concentration read must stay inside one share class.

