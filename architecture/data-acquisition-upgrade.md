# Data Acquisition Upgrade — Architecture Blueprint

- status: P1 + P2 implemented; wiring + fundamentals deltas + methodology trial done (PR #74); candidate-list screening capability added
- last_updated: 2026-06-10
- scope: the executable substrate beneath Mira's data contract (sources, routing gates, ingestion, evidence, calculation ledger)
- non_goal: redesigning the data model. The model is already strong; this fills the empty execution slots it already names.

## 0. Positioning (lite + supplementary — not a data vendor)

This is a **lite-level** fetch / process / analyse layer built on **free public
data** (SEC, Yahoo, BLS). Its purpose is to give ordinary users *some*
reproducible data support that strengthens analysis — **not** to replace
commercial feeds (Bloomberg / FactSet / Refinitiv) or a user's own more-reliable
proprietary data.

Consequences (already enforced; stated here so they are never lost):

- The data is delayed / secondary / daily by nature. The evidence tiers encode
  exactly that (Yahoo = L5 `market_pricing`; aggregator fundamentals = secondary,
  "verify vs filing"). The rigour lives in the **discipline**, not in any claim of
  vendor-grade data.
- Every conclusion inherits the `delayed` / `secondary` / `source_gap`
  limitations; the substrate must never present itself as authoritative.
- Stay lite: free keyless sources, stdlib core, no database, no comprehensive
  market-data platform. When the user has better proprietary data, that wins —
  this layer steps aside or becomes a cross-check; it does not compete.

## 1. Core Diagnosis

Mira's data-layer **specification** is institutional-grade — source taxonomy, evidence tiers,
ingestion contract, canonical field families, calculation ledger, reproducibility rules. The
**execution layer is missing**: there is no research-data fetcher, no tagger, no artifact emitter.
The contract is therefore satisfied by hand, which makes the reproducibility it demands hard to
achieve and easy to fake.

This upgrade is **"make the existing contract executable"**, not "add new structure". Every
component below fills a slot the spec already defines but never implemented. That keeps it aligned
with the repo's anti-over-engineering posture (净减负): less hand-labour, fewer reproducibility gaps,
no new concepts.

### Precise claims (corrected — do not overstate)

- **Not "zero network I/O".** `scripts/check_updates.sh` already does `git fetch` for the protocol
  freshness check. What is absent is a **research/market-data fetcher** — nothing pulls prices,
  fundamentals, or macro series.
- **The validator already enforces ledgers at the evidence-log level.**
  [`scripts/validate_repo.py:678`](../scripts/validate_repo.py) requires any
  `claim_type=derived_calculation` row to carry a `Formula:` note **or** a `calculation-ledger.csv`
  row with a matching `evidence_log_ref`, plus non-empty `upstream_sources` (line 663). The real
  gaps are narrower:
  1. **Ingestion artifacts are unvalidated** — `dataset-manifest.json`, `ingestion-log.csv`,
     `field-map.yaml`, `connector-registry.yaml` exist only as `{{placeholder}}` templates; nothing
     produces or checks them.
  2. **Derived fields in side-CSVs escape claim-typing.** `cases/*/financial-snapshot.csv` and
     `peer-comparison.csv` carry `yoy_change` / `qoq_change` / peer ratios as flat columns that are
     never represented as `derived_calculation` evidence-log rows, so the line-678 check never sees
     them. (Whether they even *need* a ledger depends on §8.)
- **Yahoo v8 is not fact-grade.** It is a reproducible **L5 `market_pricing`** substrate. Only
  official sources (SEC companyfacts, BLS/FRED/BEA) are fact-grade. The substrate must tag it
  accordingly and never let an aggregator value anchor a fundamental fact.

## 2. Current-State Map

### Source layer — `data/source-registry.csv` (74 rows)
- Only **~8 `public_api`** rows, concentrated in SEC (submissions / companyfacts / frames),
  FRED / BLS / BEA, and KR OpenDART — i.e. **filings + macro, mostly US (+KR)**.
- Price, aggregator-fundamentals, estimates, options, ownership are all **`web_read` delayed pages,
  read by hand**.
- Fact-grade fundamentals exist for **US only** (SEC companyfacts). Non-US = portals (manual) or L5
  aggregators.
- No source at all for short interest / days-to-cover, commodities / futures, FX (only inside macro
  reports), credit.
- Stale note: `stooq_history_csv_endpoint` is described as a "simple keyless CSV" but now returns a
  JS proof-of-work anti-bot wall from scripted clients (verified 2026-06-09). Must be re-annotated.
- Registry is polluted with dated, ticker-specific case rows (AAPL / COHR artifacts) that inflate it.
- Taxonomy inconsistencies: `twse_market_statistics` tagged L2 vs the L5 market-data norm; company
  marketing blogs rated L1/A; a transcript row tagged L1 while its basis says L1/L4.

### Routing / gate layer — `loops/analysis-routing.md`
- The four data gates (Step 3.3 information-value, 3.35 live-data, 3.4 ingestion, 3.5 quant) are
  well-designed **prose**, with enum vocabularies in `schemas/vocab.json`, but are **not in the
  machine-first router** `data/routing-index.csv` (which is `task_mode`-only).
- **Lazy-load tension:** the stated optimization "load only the one loop on hit" can **bypass the
  data gates entirely**, because the gates live only in the full prose spine.
- No route/token names technical-analysis, market-data fetch, or fundamentals fetch.
- Gates demand fields no tool produces (`quote_time`, `cross_check_status=passed`, "two independent
  sources"), so by-hand execution is unverifiable.

### Ingestion / evidence layer — `data/ingestion-layer.md`
- A full **adapter pattern** is specified (`provider adapter -> canonical data contract -> Mira
  evidence/calculation layer`) with `connector-registry.yaml` + `field-map.yaml` templates — and
  **zero adapter implementations**; no `urllib`/`requests` anywhere in research code.
- Canonical families are already enumerated: `market_price`, `company_financials`,
  `valuation_snapshot`, `consensus_estimate`, `estimate_revision`, `transcript_claim`,
  `ownership_short_interest`, `options_surface`, `portfolio_position`, `macro_series`,
  `issuer_disclosure` (added for A-share L1: 公告/业绩预告/回购/减持/中标/诉讼 are neither
  reported financials nor management claims, so neither `company_financials` nor
  `transcript_claim` fits them).
- Evidence-log v1.2 schema (22 cols) and calculation-ledger schema (14 cols) are defined and stable.

## 3. Constraints To Respect (do not break)

- **Machine-first routing + vocab.** `validate_repo.py::validate_routing_index` requires
  `routing-index.csv` `task_mode` ⊆ `schemas/vocab.json` enum, full coverage, existing+executable
  targets. Any new token must land in `vocab.json` first.
- **Routing-card schema.** `schemas/routing.schema.json` conditionally requires fields; new tokens
  must round-trip through it.
- **Evidence-log is claim-level.** Ingestion artifacts and ledgers **map to** evidence rows, never
  replace them. Derived numbers need `claim_type=derived_calculation` + `upstream_sources`.
- **`private/` vs tracked boundary.** User holdings / restricted / vendor-raw default to gitignored
  `private/`; only de-identified, approved promotions enter tracked `cases/`/`memory/`.
- **justfile contract.** "Thin wrappers over existing stdlib scripts — no new logic, zero
  third-party deps." Core substrate must be **stdlib-only**; `yfinance` is an optional escalation,
  never a core dependency. See [[project_mira_arch_principles]].
- **DATA_POLICY.** Registry rows are on-demand read entrypoints, not a licence to subscribe, schedule,
  bulk-crawl, or persist. Retained data must pass the ingestion layer first.

## 4. The Upgrade — A Portable stdlib Data Substrate

Location: `tools/mira_data/` (a stdlib package; runnable via `python3 -m mira_data.<cmd>` and thin
`justfile` recipes). Each layer fills a named-but-empty spec slot.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ tools/mira_data/   (stdlib core, zero deps; yfinance = optional escalation)│
└──────────────────────────────────────────────────────────────────────────┘

① fetchers  = the specified-but-unimplemented "provider adapter → canonical contract"
   company_financials  ← SEC companyfacts  (keyless, US)     evidence tier: fact / L2
   macro_series        ← BLS (keyless)                       evidence tier: fact / L2
                         FRED / BEA (free key)  → deferred until key mgmt exists
   market_price        ← Yahoo v8 chart (keyless)            evidence tier: market_pricing / L5
   [optional] estimates / options / ownership / non-US screening ← yfinance  → secondary / L5

② tagger    = stamp every field with the registry's EXISTING posture
   source_id · source_class · authority_level · content_type/claim_type ·
   evidence_category · as_of_date / source_date · latency_class
   → Yahoo revenue auto-tagged secondary/screening; companyfacts auto-tagged fact/L2.
     This is what makes "cover fundamentals too" safe: nothing can masquerade as a fact.

③ compute   = reproducible derived numbers (technical indicators first consumer; YoY/QoQ/CAGR next)
   pure stdlib math/statistics → auto-emit calculation-ledger.csv rows (per §8 trigger rule)

④ emitters  = the artifacts the spec demands and humans currently hand-type
   dataset-manifest.json · ingestion-log.csv row ·
   evidence-log.csv v1.2 rows (pre-tagged claim_type/evidence_category) ·
   calculation-ledger.csv rows

⑤ wiring    = pin the substrate into routing as a CROSS-CUTTING CAPABILITY (not a task_mode)
   data_acquisition_required / data_acquisition_plan / technical_context_required (fields/overlay)

⑥ validator = extend validate_repo.py to check the new artifacts and their cross-references
```

## 5. Routing Integration — capability, not task_mode

Data acquisition is a **cross-cutting capability**, not a research task. Do **not** add a `task_mode`
for "data-fetch". Instead introduce capability fields / an overlay, registered in `schemas/vocab.json`
and surfaced where the data gates already run (Steps 3.35 / 3.4 / 3.5):

- `data_acquisition_required`: `none` / `optional` / `required`
- `data_acquisition_plan`: which canonical families + which adapters + freshness target
- `technical_context_required`: `no` / `yes` (drives the technical-context overlay)

This also resolves the lazy-load-vs-gates tension: the capability field gives the machine router a
hook to fire the data gates, instead of relying on the model reading the full prose spine.

## 6. Phasing

### P1 — Foundation MVP (narrowed; ships with validator teeth)
- **SEC companyfacts adapter**: official fact. Output canonical `company_financials` with
  taxonomy / tag / unit / period / frame preserved.
- **BLS adapter**: official macro, **keyless first**. FRED / BEA deferred until key management exists.
- **Yahoo v8 chart adapter**: **L5 `market_pricing`** only — explicitly **not** a fundamentals fact
  source.
- **Emitter**: auto-produce `dataset-manifest.json`, `ingestion-log.csv` rows, evidence-log rows,
  calculation-ledger rows.
- **Validator (moved forward from P3 — minimal set, ships with P1):**
  - manifest exists for a retained fetch;
  - `source_id` is valid (registry or a newly-added adapter source row);
  - `field family` ∈ the canonical enum;
  - every ledger row references a real evidence row;
  - every derived number carries input sources.
  Rationale: without teeth in P1, P1 output can drift exactly like the hand-maintained layer it
  replaces.

### P2 — Compute + technical context
- **Done:** stdlib indicator engine (`indicators.py` + `technical.py`) consuming `market_price`;
  fills `technical-analysis-check.csv` + emits ledgered derived records.
- Next: YoY / QoQ / CAGR / peer deltas (same derived-record path); `technical-context` overlay
  wired via the capability field (§5).
- Run the `technical-analysis` methodology trial (cohr, crwv earnings; aapl liquidity; one
  failed-breakout monitoring case; one ETF case) and decide adopt / keep-trial.

### P3 — Pin + clean
- Full router machine-hook for the data gates.
- Broader validator coverage.
- Registry cleanup: re-annotate Stooq, purge dated case rows, fix the L2/L5 + blog-L1 + transcript
  inconsistencies, and collapse the four overlapping freshness fields
  (`live_freshness_status` / evidence `freshness_status` / `as_of_date` / `price_date`) into one map.

### P4 — Optional breadth
- `yfinance` escalation adapter (estimates / options / ownership / non-US screening).
- More exchange APIs (TWSE JSON, extended OpenDART).

## 7. Evidence-Tier Discipline (the safety rule)

The substrate's value is not "more numbers" — it is that **every field is born with the correct
evidence tier**:

| family | adapter | claim_type | evidence_category | authority |
| --- | --- | --- | --- | --- |
| company_financials | SEC companyfacts | `reported_metric` / `fact` | `reported_fact` | L2 |
| macro_series | BLS | `fact` | `reported_fact` | L2 |
| market_price | Yahoo v8 | `market_pricing` | `market_pricing` | L5 |
| company_financials (screening) | yfinance | `reported_metric` (screening) | `reported_fact` but **secondary** | L5 |
| consensus_estimate | yfinance | `forecast` | `estimate` | L5 |

Aggregator fundamentals are screening-grade and must be verified against filings before anchoring a
durable conclusion (DATA_POLICY: "not a substitute for SEC or issuer filings").

## 8. Calculation-Ledger Trigger Semantics (critical nuance)

A `calculation-ledger.csv` row is required **only when Mira itself derives a number that affects a
judgment**. It is **not** required for values an issuer already disclosed.

- Company-disclosed YoY / QoQ / margins → `claim_type=reported_metric` / `fact`, sourced to the
  release. **No ledger.**
- Mira-computed deltas, ratios, peer ranks, relative returns, indicators that feed `thesis_impact`,
  `actionability`, or scenario math → `claim_type=derived_calculation` → **ledger required** (and the
  emitter must produce both the evidence row and the ledger row).

The emitter must therefore **distinguish disclosed vs computed** at the point of emission, and only
ledger the latter. This also tells us how to treat the existing `financial-snapshot.csv` deltas: if
disclosed, tag as reported; if Mira-computed, promote to a `derived_calculation` evidence row + ledger.

## 8b. Configuration — local contact + API keys

User-specific contact and API keys live in a gitignored env file, never in
tracked files (the private-state boundary). Standard location and format:

```sh
cp templates/mira-data-config.example private/mira-data.env
# then set MIRA_CONTACT_EMAIL=you@your-domain.com
```

Resolution order (first hit wins): environment variable > `MIRA_DATA_CONFIG`
file > `private/mira-data.env` > `~/.config/mira/mira-data.env`. Format is simple
`KEY=value`, parsed by stdlib — no PyYAML / tomllib dependency. Keys:
`MIRA_CONTACT_EMAIL` (+ optional `MIRA_CONTACT_NAME`) or a full `MIRA_HTTP_UA`;
optional `FRED_API_KEY` / `BEA_API_KEY` for keyed macro sources.

**Official-data gating.** SEC's access policy requires a contactable User-Agent
(it rejects no-email UAs and blocks some domains, e.g. `noreply.github.com`).
Adapters that hit SEC refuse to run under the shipped placeholder and return a
`source_gap` with setup instructions; configuring a real contact unlocks them.
`python3 -m mira_data config` prints the resolved status. This keeps Mira from
fetching official data under a fake identity, while staying zero-friction for
keyless sources (Yahoo / BLS) that need no contact.

## 8c. P1 Status & Usage (implemented)

Implemented in `tools/mira_data/` (stdlib core, zero third-party deps):

| family | adapter | evidence tier | command |
| --- | --- | --- | --- |
| company_financials | SEC companyfacts | reported_metric / L2 | `just data-fetch company_financials AAPL` |
| market_price | Yahoo v8 chart | market_pricing / L5 | `just data-fetch market_price AAPL` |
| macro_series | BLS (keyless) | fact / L2 | `just data-fetch macro_series CUUR0000SA0` |

Each fetch emits a bundle into the output dir: `dataset-manifest.json`,
`ingestion-log.csv`, `evidence-log.csv` (v1.2 — passes `scripts/validate_repo.py`
unchanged), and, for series families, a bulk OHLCV / observation CSV the manifest
points at. `just data-validate <dir>` runs the bundle checks; `just data-config`
shows the resolved contact. SEC fetches are gated on a configured contact (§8b).
The `derived`/ledger path is wired but unused until the P2 compute engine: issuer-
disclosed and market values emit `ledgered=0` (§8).

Verified end-to-end against live SEC / Yahoo / BLS endpoints; all emitted evidence
rows are `validate_repo`-clean (the period-aware SEC selection and the required
`source_speaker` field were the two correctness fixes found during the build).

**Screening capability — implemented.** `just data-screen "AAPL,MSFT,..."
"--min-fcf-yield 0.04"` (`python3 -m mira_data screen`) upgrades the
`discovery_or_screening` fallback ("no skill → watchlist + source_gap") into an
execution path. Deliberately lite: bounded candidate-list triage (≤30 tickers,
explicit list — never a market crawl, per DATA_POLICY) over dimensions the
existing substrate already supports — market cap, FCF yield (FY OCF − FY capex,
which added a `capex` curated tag to the companyfacts adapter), debt/equity,
net margin, revenue YoY; flow metrics use the latest complete FY to avoid the
companyfacts Q2/Q3 YTD trap. Output: `screening-watchlist.csv`
(templates/screening-watchlist.csv schema) plus, for passing tickers, ledgered
L6 derived ratios over SEC L2 + Yahoo L5 upstreams. A failed single-ticker
fetch degrades that row to `data_gap`; a fully failed run exits as
`source_gap` so routing falls back to the watchlist-note path. Whole-market
factor screens (SEC frames cross-sections) and non-US / estimates screening
stay deferred (P4).

**Cash-flow span option — implemented.** Issuers file Q2/Q3 cash flow as 6-/9-month
year-to-date spans, so `fetch company_financials` defaults to the last clean
single-quarter span: correct, but able to lag the income statement by two quarters
(AAPL FY2026 Q3 revenue sitting next to FY2026 Q1 operating cash flow).
`--cash-flow-span ytd` reads the newest cumulative filing and differences the prior
cumulative row of the same fiscal year (9M − 6M) into the latest quarter. That
difference is Mira-computed, so it is emitted as a ledgered `derived_calculation`
with both spans named in the formula. A difference that is not one quarter wide
(9M − 3M) is labeled by its own span instead of being passed off as a quarter, and a
metric with no prior cumulative row stays as filed under an explicit `9M` label.
Every snapshot claim now records `period_end`, `span_days` and `conversion`, so the
basis travels with the value; screening exposes `cash_flow_period_end` and
`revenue_yoy_period` because one screen row combines annual flows with a quarterly
YoY.

**A-share L1 disclosure channel — implemented.** `python3 -m mira_data fetch
cninfo_announcements 600519 --since 2026-01-01` reads the CNINFO (巨潮资讯网)
announcement index — the portal designated for mainland listed-company disclosure — and
emits one `issuer_disclosure` record per announcement: secCode + announcementId + title +
Beijing disclosure date + PDF URL, stamped L1 `issuer_primary_disclosure`. **A new
canonical family was added for this** (the eleventh): 业绩预告/回购/减持/中标/诉讼/调研纪要
are neither reported financials nor management claims, so neither `company_financials`
nor `transcript_claim` fits them. The adapter is stdlib-only (two form-POST endpoints
plus a PDF link) and needs no key, cookie or JS challenge; it absorbs four verified
traps — a server-capped `pageSize=30`; per-stock queries needing an orgId (one bulk map
request, because a bare 6-digit code returns zero rows); an invalid category code
**silently returning the whole market** instead of erroring, so categories are
whitelisted and the known-bad codes raise; and `announcementTime` being a UTC epoch whose
date must be read in Asia/Shanghai or it lands a day early. Event tokens come from
`announcementType`, which is a pipe-delimited **multi-label** code list rather than the
single opaque code public write-ups describe; it has no public ontology, so only observed
codes are mapped and everything else degrades to title rules and finally to `other`.
Dedupe keys on (secCode, Beijing date, normalized title): the same PDF can be indexed
twice, while the same title on different dates is two real announcements.

**P2 compute engine — implemented.** `just data-technical AAPL` (benchmark
defaults to SPY) consumes the `market_price` series and computes the daily subset
of `technical-analysis-check.csv` in pure stdlib (`indicators.py` + `technical.py`
— no numpy/pandas/db; single-name scale needs none). It derives the state tokens
(`trend_state`, `ma_stack_state`, `volume_state`, `volatility_state`,
`positioning_risk=source_gap`, `technical_context_score`), writes the 64-col
check row, and emits a curated set of judgment-affecting **derived** records —
which exercises the other side of §8: `ledgered=5`, every ledger row backed by a
`derived_calculation` evidence row with `upstream_sources`, all `validate_repo`-
clean. Options / short-interest / intraday stay `source_gap` (no free source).

## 8d. A-share L1–L5 channels (implemented)

Status note for §2: that diagnosis predates the substrate work. The registry now holds
**111 rows** (35 of them `public_api`); `tools/mira_data/` ships **44 fetch families, 31 usable
with zero optional dependencies** — 8 take `futu-api` / `ib_insync` (local gateways) and 5 take
`xlrd` (`index_members`, `index_valuation`) or `pypdf` (`shareholder_count`, `ir_activity`,
`lockup_change`), each through a lazy import that degrades to a labelled gap; and
**31 offline suites** run inside `scripts/run_quality_gate.py`. §8c documents the US channels;
this section documents the mainland ones, which have their own traps and their own tier split.

| layer | family | adapter / registered source | tier | command |
| --- | --- | --- | --- | --- |
| L1 | issuer_disclosure | CNINFO 公告索引 (`cninfo_announcement_api`) | issuer_primary_disclosure | `mira_data fetch cninfo_announcements 600519 --since 2026-07-01` |
| L2 | macro_series | NBS 数据发布库, 13 series (`nbs_stats_api`) | fact | `mira_data fetch macro_nbs ALL` |
| L2 | macro_series | CFETS LPR / Shibor (`chinamoney_rates_api`) | fact | `mira_data fetch macro_rates BOTH` |
| L2 | ownership_short_interest | SSE / SZSE margin (`sse_margin_api`, `szse_margin_api`) | fact | `mira_data fetch margin_balance 600519` |
| L2 | transcript_claim | 互动易 / 上证e互动 (`irm_cninfo_api`, `sse_einteraction_api`) | company_claim | `mira_data fetch investor_qa 000001` |
| L1 | ownership_short_interest | CNINFO filed report body — 股东户数 (`cninfo_announcement_api`) | reported_metric | `mira_data fetch shareholder_count 600519` |
| L1 | transcript_claim | CNINFO 投资者关系活动记录表 — announcement feed, then the full-text search index for Shenzhen / HK-line copies (`cninfo_announcement_api`) | company_claim | `mira_data fetch ir_activity 301277` |
| L5 | ownership_short_interest | Eastmoney 十大股东 / 十大流通股东 relay (`eastmoney_shareholders_api`) | reported_metric | `mira_data fetch shareholders_top10 600519` |
| L5 | ownership_short_interest | Eastmoney 限售解禁 schedule relay (`eastmoney_lockup_api`) | reported_metric | `mira_data fetch lockup_schedule 300750` |
| L5 | ownership_short_interest | Eastmoney 董监高持股变动 relay (`eastmoney_insider_api`) | reported_metric | `mira_data fetch executive_holdings 600519` |
| L1 | ownership_short_interest | CNINFO periodic report body — 限售股份变动情况 (`cninfo_announcement_api`) | reported_metric | `mira_data fetch lockup_change 688041` |
| L3 | consensus_estimate | Eastmoney 盈利预测 (`eastmoney_consensus_api`) | forecast | `mira_data fetch consensus_estimate 600519` |
| L5 | market_price, company_financials | licensed hithink-finance CLI (`hithink_finance_api`) | market_pricing / reported_metric | `mira_data fetch hithink_market_price 600519` |
| L5 | options_surface | ETF options over the same CLI (`hithink_finance_api`) | market_pricing | `mira_data fetch option_surface 510050` |
| L5 | macro_series | Eastmoney national-accounts relay (`eastmoney_macro_api`) | reported_metric | `mira_data fetch macro_china ALL` |

Two families were added after this table was first written, both mainland and both official:
`macro_region` (provincial GDP and household income over the agency's 31-province and 71-city
catalogues, read as a period x region matrix) and the CSI index families — `index_benchmark`
(official index history and profile), `index_members` (composition and weights) and
`index_valuation` (the compiler's own P/E and dividend yield). Together they close the two gaps
the protocol names explicitly: the benchmark denominator (`benchmark_ohlcv` in
`data/public-source-targets.md`, plus the `relative_return_*` columns of the technical check
template) and the valuation anchor the A-share market-structure gate requires. The index
composition and valuation families read the compiler's **OLE2/BIFF .xls** workbooks, so they
take `xlrd` as an optional dependency (lazy import, `csindex_dependency_gap` without it) in the
same spirit as `futu-api` / `ib_insync`; the workbooks are parsed positionally because their
header rows are bilingual, the weights file can carry a different date from the constituent
file, and the valuation workbook holds only the latest ~21 observations — all three recorded in
provenance rather than assumed away.

Every channel above walks the same contract: a `data/source-registry.csv` row, a 1:1
`data/source-class-map.csv` row, a `POSTURES` entry, a `SOURCE_POLICY` entry, an offline
suite registered in the quality gate, a clean `validate_repo.py`, and a bundle whose
`evidence-log.csv` passes unchanged.

**Official macro is the agency's own library — after this document's first attempt got it
wrong.** An earlier probe here concluded the NBS endpoints needed TLS fingerprinting and were
out of reach for a stdlib client. That was wrong, and the correction matters more than the
adapter: the probe used **GET** against the *retired* paths (the library was replaced on
2026-03-27; the announcement "关于新版国家统计局数据发布库上线的公告" says so, and the old
`easyquery.htm` now 403s). The successor answers a **POST** with a JSON envelope, keyless,
behind nothing but a browser User-Agent and Referer — 2.8 MB of JSON over plain `urllib`.
Thirteen series ship (CPI, PPI, the PMI trio, industrial value added and revenue,
unemployment, retail sales, fixed-asset investment with its three sector splits, real-estate
investment, new-home sales, household income, GDP), each stamped L2 `fact` with the
statistical basis (口径) attached. Four traps are absorbed and tested: unpublished periods
come back as the literal string **`无`** rather than being absent, so non-numeric values are
dropped instead of read as zero; indicator ids are opaque UUIDs and one series can be **split
across catalog ids**, so each record names the cid/indicator/root it read and states that one
call is not the whole history; the 口径 text exists only in the catalogue endpoint, so it is
fetched once per catalogue and a failure there costs the note, never the data; and the library
sits behind a Wangsu WAF that answers an uncookied request with an **intermittent 307**, so
`net.py` grew a cookie-keeping `Session` with a warm-up and 307 joined the retryable statuses.
Two gaps are documented rather than filled: total industrial profit is named by two catalogue
slices that serve no current data (one empty across 2011-2026, one stopping at 2011-12), and
money supply is central-bank data, not a statistical-agency series. The aggregator relay
(`eastmoney_macro`) stays as the cross-check rather than being replaced: it already carried
September PMI while the official library still returned `无` for that period, and where both
publish, the prints agree (CPI 100.8 ~ +0.8%, PPI 103.8 ~ +3.8%, GDP 695,704 亿元).

**Investor Q&A: the channel is official, the content is not.** 互动易 and 上证e互动 are
exchange-designated platforms, so the channel is L2 `regulatory_and_exchange`, but what they
carry is company commentary, so the claim stays `company_claim` / `company_statement` — a
company's answer is 公司口径 that still needs cross-checking, not a verified fact. Coverage
follows the exchanges and cannot be merged: 互动易 is the Shenzhen platform (000001 returns
Q&A; 600519 and 688111 return zero rows), Shanghai names come from 上证e互动, and Beijing
names have neither and degrade without spending a request. Unanswered questions are **not**
emitted — investor attention is not company speech. The claims are extracts (200-character cap
plus the platform URL for the full text, per the ingestion layer's "extract claims, not long
text" rule), and e互动 needs the company uid found by binary-searching a code-sorted directory
instead of pulling all 73 pages.

**Exchange margin: two venues, two unit systems.** SSE publishes named fields in 元/股; SZSE
publishes abbreviated fields in 亿元/万股 and, on one table, a securities-lending balance that
was wrong by 10,000x until the unit factor was inferred from the vendor's own identity
(融资余额 + 融券余额 = 融资融券余额) instead of assumed. The two exchanges also do not always
publish the same day, so the adapter walks back within a bounded window and records both the
requested and the used date. Beijing-exchange names are uncovered by this endpoint.

**Options: priced, but not an implied-volatility surface.** A-share ETF options come from the
licensed CLI (contracts / contract-detail / daily). The strike is read from contract-detail
rather than parsed out of the vendor ticker, one unpriced contract degrades alone, and every
record states `notPublished=open_interest,implied_volatility` — the endpoint publishes closing
prices and volume, so the surface supports a straddle or a put/call volume read but **cannot**
support OI, IV rank or skew.

**Listed funds are symbols too.** Extending `resolve_thscode` to Shanghai 5xxxxx and Shenzhen
15xxxx/16xxxx/18xxxx codes was not cosmetic: ETF codes reach the quote, announcement and
option paths, and they were being rejected as invalid symbols.

**Filing bodies, not just the index — implemented.** `mira_data cninfo text 600519 --title
年度报告` reads the filed document itself: it finds the announcements whose title matches, then
extracts the PDF text layer (verified live on a 110-page 半年度报告: 4,737 characters from the
first six pages, 2,482 from the 摘要). PDF parsing needs the optional dependency `pypdf`
(lazy import, `cninfo_dependency_gap` without it). The honesty rule is what the tests pin: an
image-only scan reports `cninfo_pdf_text_gap` rather than an empty string, because an empty
extraction reads exactly like "the filed document does not contain this section"; an oversized
body reports `cninfo_pdf_too_large` with its size; unparsable bytes report
`cninfo_pdf_unparsable`; a single failing page is marked inline (`[[page N extract failed]]`)
instead of discarding the readable pages; and truncation is reported with the page counts. This
closes the gap that mattered most in the L1 review — the substrate had index-level L1 and no
access to the口径原文, the 分部拆分 or the risk-factor wording.

Of the three structured fields the same review named, **all three are now covered** — two from the
body and one from a relay, each by the route that actually works:

- the shareholder **count** is lifted out of the PDF text layer (below);
- the **前十名股东 / 前十名流通股东** tables come from the aggregator relay
  (`shareholders_top10` / `shareholders_free_float`, §8d) rather than from the text layer, where
  their headers split as `期末持股数 量` and the values lose column binding;
- the **限售股份变动情况** table is parsed out of the periodic report body by
  `fetch_lockup_change` (§8d). This one *is* recoverable from the body, but only after measuring
  which extractor works — see that section for why the plain text layer destroys it and why the
  issuer's own 合计 row is a mandatory checksum rather than a nicety.

**Shenzhen IR-activity records: the earlier "unreachable" conclusion was wrong, and the error is
instructive.** An earlier revision of this document recorded that Shenzhen names do not file
投资者关系活动记录表 as announcements and that reaching them would need a browser-grade renderer or
a licensed route. The *observation* was right and the *inference* was wrong: they are not in the
announcement feed, but they are in the portal's **full-text search index**, which is a different
index on the same public, keyless host. `mira_data fetch ir_activity 301277` now returns 新天地's
Shenzhen records.

The five probes that produced the false conclusion are kept here because every one of them was
aimed at the wrong layer, and the pattern is worth recognising:

1. ten sibling paths under `irm.cninfo.com.cn` (survey/companySurvey/querySurveyInfo/…) → all 404;
2. the **announcement feed** (`hisAnnouncement/query`) for large Shenzhen caps → zero matching
   titles in a full quarter. Correct, and irrelevant: the feed does not carry this document type
   for Shenzhen at all. Re-verified 2026-10-03 on 亿道信息, whose 2026-09 window holds 17 rows
   and not the 2026-09-08 IR record that demonstrably exists on `static.cninfo.com.cn`;
3. the 互动易 platform-wide search across five keywords → every hit was investor Q&A. This probe
   was **not actually searching**: re-tested 2026-10-03, the keyword is ignored entirely (a
   nonsense keyword and a real one return the same `totalRecord` 62829 and the same first row), so
   the conclusion "there is no survey document type in that index" never had support;
4. the four `attachedId`/`esId` detail routes → all 404;
5. the SPA bundles → only authentication routes. Still true, and still irrelevant: the documents
   were never on that platform.

**The index that does carry them.** `POST /new/fulltextSearch/full` (form-encoded, public,
keyless) with `searchkey=投资者关系活动记录表` returns ~1020 records going back to 2018-03,
including Shenzhen codes (301277, 002486, 002573, 002921, 301584, …) and the 5-digit HK-line
"海外监管公告" copies of A+H issuers. Contract traps absorbed: the keyword field is `searchkey` and
is genuinely honoured (nonsense → `total` 0); `pageSize` is **silently capped at 100** and the
reported page count shrinks to match, so a larger request only misreports; and `sdate`/`edate`
filter the result set while `total` then counts only the window, which makes an empty-keyword date
sweep a usable corpus walk. The HK rows are filtered out by code, since a 5-digit HK code is a
different security from the A-share line.

**Coverage gain is real but small — measured, after an initial overstatement.** The search index
carries only a handful of Shenzhen names (301277, 002486, 002573, 002921, 301584, …) plus the
5-digit HK-line 海外监管公告 copies. The bulk of Shenzhen is still absent: a 16-name sample of
large Shenzhen issuers (000001, 000002, 000333, 000651, 000725, 000858, 002230, 002352, 002415,
002475, 002594, 300059, 300124, 300308, 300750, 300760) returns **zero** IR records in
2025-01..2026-10 from either index, and 宁德时代 has none in its first 1000 search hits. Inside the
index's own IR corpus the split is lopsided: 3 Shenzhen records out of 44 for 2026-07..09, against
29 Shanghai. So the honest statement is that this channel covers Shanghai filers via the feed and
**a narrow slice** of Shenzhen via the search index — not that Shenzhen is solved. 美力科技
(300611) and 科大国创 (300520) have publicly readable records that neither index returns.

The route that would actually close the Shenzhen bulk was mapped and set aside:
`dataclouds.cninfo.com.cn/shgonggao/` is an S3-style bucket with a public date listing
(`investor/{year}/{yyyymmdd}/{hash}.PDF`, ~19-77 docs/day) whose PDFs print `股票代码` on page 1.
It has no code→document index, so it needs a full-corpus crawl (~100 docs/day at ~234 KB each, and
Range requests do not work because pypdf needs the trailing xref, so any truncation fails to
parse). That is a cost decision for later, not a probing one — and it is the only route found that
would cover the Shenzhen bulk.

**Shareholder structure: the tables the filing body refuses, read from a relay instead.**
`mira_data fetch shareholders_top10 600519` and `shareholders_free_float 300750` carry the
issuer's own 前十名股东 and 前十名流通股东 tables — the ownership input
`market-structure-policy.md` asks for, and the one the PDF text layer cannot supply (its headers
split as `期末持股数 量` and the values lose column binding, so the adapter claims only the count
and states the refusal). The relay publishes the same table already bound to its columns, which
is why this is a second channel rather than a cleverer regex.

Three things are pinned by tests rather than left to review:

- **The ratio basis travels with the value.** The two tables publish shares of 总股本 and shares
  of 流通A股 — different denominators for the same holder — so the records are never merged, the
  rank metric names differ (`holder_top10_rank` vs `holder_free_float_rank`), and `ratioField` /
  `ratioBasis` say which denominator each percentage uses.
- **Units are checked, not assumed.** `HOLD_NUM` is a raw share count: 600519 2026-06-30 rank 1
  is 681,282,935 over `TOTAL_SHARES_NUM` 1,250,081,601 = 54.4999%, matching the published 54.5,
  and the same arithmetic holds on 000001 (49.56), 688111 (51.38) and 300750 (22.04). A ratio
  that did not reconcile with the published share count would have been the bug.
- **Nothing is derived.** No sum, concentration ratio or float percentage is computed — those
  would be Mira calculations owing a ledger row (§8) — and an empty vendor field stays absent
  rather than becoming a zero, which is why a 不变 / 新进 state never turns into a 0-share change.

The relay takes an `END_DATE` equality filter but cannot be asked for "the latest period", so the
newest period is probed first and then pinned by equality; a single page would otherwise mix two
periods and repeat ranks. Tier: the content is the issuer's disclosed table (L1) but the publisher
is an aggregator, so records are L5 `reported_fact` and every one names the issuer's filing as the
L1 upgrade route — the same treatment `em_insider` and `em_lockup` get.

**限售股份变动情况: the one filing table that is genuinely recoverable from the body.**
`mira_data fetch lockup_change 688041` reads the issuer's per-holder restricted-share movement out
of the periodic report body — 年初限售股数, 本年解除限售股数, 本年增加限售股数, 年末限售股数, plus the
限售原因 and 解除限售日期 that travel with each holder. This closes the last field of the L1 review's
three.

**Which extractor was an empirical question, and both obvious answers are wrong.** Measured against
live filings rather than assumed:

- the **plain** text layer destroys the table — its header arrives as fragments (`是否有 履行期 限`)
  and the numbers lose their column binding entirely;
- **layout** mode (`extraction_mode="layout"`) keeps each row on one line and *is* usable here, but
  it still glues adjacent cells in the 合计 row (`1,437,780,9101,437,780,910`);
- **coordinates** (`visitor_text` + clustering) were prototyped too. They do preserve cell
  boundaries, but a visual row is emitted as *two* clusters ~0.6pt apart (numeric cells at
  y=454.6, the holder-name and reason cells at y=454.0), so any single global row-band tolerance
  wide enough to merge them also merges the neighbouring row 15.7pt away.

Layout mode won because the *data* rows are space-separated; the coordinates path was more
machinery than this table repays.

**The checksum is the safety property, and it is mandatory.** The issuer's own 合计 row is used as
a check, never as a claim, under two invariants: per holder ``年末 = 年初 - 本年解除 + 本年增加``, and
the holders' columns sum to the published total. Three rules follow, all pinned by tests:

- a table whose 合计 row cannot be parsed is **refused** (`cninfo_lockup_check_missing`), because
  "no checksum available" must not mean "emit anyway". This is not hypothetical: the first working
  draft unpacked the four numeric columns right-to-left instead of in published order, producing
  opening=0 / added=649,900,000 for a row the filing states as opening=649,900,000 / added=0. It
  looked entirely reasonable, and only the checksum caught it.
- a table that does not reconcile is refused (`cninfo_lockup_check_failed`) rather than averaged;
- the glued 合计 token is repaired structurally (two equal halves, or halves matching published
  numbers), and an ambiguous split yields a gap instead of a guess. Thousands separators are
  validated as proper 3-digit groups, because a looser `[\d,]+` accepts the glued pair as one
  number — which silently defeated both the checksum and its repair.

The page window is widened for this read (page 103 of a 231-page report) and the **annual** report
is preferred over the newest periodic one, because an issuer can mark the section 适用 in one
report and 不适用 in the next: 海光信息's FY2025 annual report carries the table while its H1 2026
report does not, so "newest body" alone reports a false gap. Verified against 海光信息 FY2025 — six
holders, 1,437,780,910 shares released — which matches the standalone 限售股上市流通公告 for the same
event. An issuer whose section is 不适用 yields `cninfo_lockup_not_applicable`, not an empty holder
list.

**FRED and BEA: the keyed US macro publishers, wired once the key gate was built.**
`mira_data fetch macro_fred DGS10` and `macro_bea T10101 --dataset NIPA --frequency Q` close the
"official macro is BLS-only" half of §2's diagnosis. Both are L2 fact-grade like BLS, and both
require a free key, so the first thing needed was a *key gate* that behaves like the SEC contact
gate: with `FRED_API_KEY` / `BEA_API_KEY` unset the fetch degrades to a labelled
`fred_key_gap` / `bea_key_gap` naming the file to edit, never a silent empty read.

Building that gate surfaced a Python detail worth writing down, because the first two attempts
both looked right. `net` imports `config`, so `config` cannot name `net.FetchError`; a gap has to
be an instance of it, since every adapter catches that class. Defining the exception in `config`
and having `FetchError` inherit it produces a correct-looking MRO in which `except FetchError`
**still misses the gap** — because what gets raised is the *parent*, and `except` on a child does
not catch its base. The fix is late binding: `net` registers `FetchError` with `config` at import,
and `require_api_key` raises whatever is registered. A test pins it, since the failure mode is
silent.

Two provider quirks were measured rather than assumed, and both would otherwise publish a wrong
answer:

- **FRED drops the connection** when a request advertises `Accept-Encoding: gzip, deflate`, which
  is what `net`'s shared helper sends. The same URL with `identity` returns the full body
  (6,398 bytes / 319 lines vs a connection error), so the adapter opts out of compression. FRED's
  missing observation is the literal `.` and is **dropped, not zeroed** — a zero is a claim the
  source never made. Vintages travel in provenance because FRED returns the currently-known
  revision by default.
- **BEA reports rejection inside an HTTP 200 payload** (`BEAAPI.Results.Error`; measured: an
  inactive key returns `APIErrorCode` 4, an unknown key code 1). A 200-only check would emit an
  empty series from a refused request, so the envelope is inspected before any data is read, and
  a credential error is split from a source error. Suppressed cells (`(D)`/`(NA)`/`(L)`/`(NM)`)
  are dropped rather than zeroed, and BEA's shared `DEMO` id is deliberately not used as a
  fallback: it is not active, so it could only ever produce a gap dressed up as a read.

**Verified live (FRED).** With a real key configured, `macro_fred` returns real observations:
DGS10 5.24 on 2026-10-01 with 23 usable rows, DFF 3.88, CPIAUCSL 334.131, UNRATE 4.2, and the
`--since/--until` window narrows the read as intended. Bundle validation is clean, and a
deliberately invalid key produces `fred_key_gap` ("may be invalid or not yet activated") rather
than a generic source gap.

**One more trap that only a live call could find: the key was being written into the evidence
log.** FRED takes its credential as a query parameter, and the adapter recorded the request URL
verbatim as `url_or_path` — a *tracked* field. The first real fetch wrote
`api_key=<the real key>` straight into `evidence-log.csv`. It was caught by reading the emitted row,
not by any test, and it is worth being precise about the blast radius: the file lives under
gitignored `private/`, and `git grep` confirmed the key never entered a tracked file or a commit.
The fix is provider-agnostic rather than FRED-specific — `net.redact_url` blanks any value whose
parameter name looks like a credential (``api_key``/``apikey``/``key``/``userid``/``token``/…,
case-insensitive, idempotent), and both adapters pass their recorded URL through it, so a future
keyed adapter inherits the protection without opting in. A regression test asserts the secret is
absent from the *recorded* field, not merely that the helper works.

Note also that `emit_bundle` **appends** to `evidence-log.csv`, so a re-run into the same `--out`
directory accumulates rows; the tainted bundle had to be deleted rather than overwritten.

**BEA verified live as well.** `macro_bea` returns real accounts: NIPA T10105 line 1 (GDP, levels)
31,906,274 for 2026-Q1, T10106 line 1 (real GDP, chained dollars) 24,274,383, and T10101 line 1
(percent change, annual rate) 2.5 — three different bases from three tables, which is the point of
the next paragraph. Bundle validation is clean and the key is redacted from the recorded URL.

**A live read also exposed a design flaw the mocked contract hid: a BEA table is not one series.**
T10105 alone returns GDP, personal consumption, goods, services, structures and more — 150 rows
across a dozen line items — while the first version emitted `metric="NIPA.T10101"` with
`unit="value"`. That made `NIPA.T10101 0.1` (a percent change) indistinguishable from a level, and
left the reader unable to tell whether a figure was GDP or "Goods". Both are now derived per row:

- the **line is part of the metric** (`NIPA.T10105.line1`), with `lineNumber` and
  `lineDescription` in provenance, because a BEA number is meaningless without them;
- the **unit comes from BEA's own `CL_UNIT` / `UNIT_MULT` / `METRIC_NAME`** instead of a guessed
  `"value"`: `Level` at `UNIT_MULT` 6 becomes `millions_usd`, `Percent change, annual rate`
  becomes `percent_annual_rate`, and `Level` + `Chained Dollars` becomes
  `millions_chained_dollars` — a bare `millions` there would imply a current-dollar basis the
  number does not have. The bulk CSV carries `line_number` and `unit` per row for the same reason.

Writing that unit parser produced two silent bugs, both caught by tests and both worth noting
because each yielded a *plausible* token rather than an error: `str.strip("_")` removes from
**both** ends, so `"index"` was mangled to `nde` and then `_ndex`; and a label already stating its
own magnitude (`Millions of dollars`) was double-prefixed into `millions_millions_of_dollars`. The
composition now joins explicitly, strips only a leading separator, and skips the scale prefix when
the label carries one.

**Futures member rankings: two exchanges wired at L2, two not reachable.** `mira_data fetch
futures_member_rank cu --venue SHFE` and `... AP --venue CZCE` read the exchange's own member
position ranking — per-instrument top members with volume, long positions and short positions
plus their day changes. The exchange is the controlling source for its own market statistics, so
this is genuine L2; the hithink CLI carries the same tables at L5, and only the L2 read can anchor
a positioning claim on its own. Both venues were probed live on 2026-10-03:

- **SHFE** publishes a JSON file per session at
  `/data/tradedata/future/dailydata/pm{YYYYMMDD}.dat` (528 KB, 1,700 rows). The three ranked
  tables arrive **side by side in one row**, and a row's instrument is either the variety
  aggregate (`cuall`) or one coded contract (`cu2610`).
- **CZCE** publishes a pipe-delimited text file per session (440 KB, 2,962 lines): one table per
  variety behind a `品种：… 日期：…` header, with `rank|member|volume|Δ|member|long|Δ|member|short|Δ`.
  It is **UTF-8** (decoding it as GBK produced mojibake on the first probe), and the variety label
  glues the Chinese name to the ticker (`苹果AP`) so the requested code is the *trailing* ascii run.

Three traps were found by probing rather than assuming, and each produced a plausible wrong
answer: matching SHFE instruments on a bare prefix swallowed 331 rows across unrelated series
(`cu` also prefixes `cual`); matching CZCE on a prefix found **nothing**, because every header
starts with the Chinese name; and collapsing the three side-by-side tables would attribute one
member's long book to a different member, since the members differ per side.

**Not wired, and the reasons are recorded rather than left as "todo":** DCE answers **HTTP 412** to
a scripted client, CFFEX serves **HTML where a CSV is expected**, and GFEX answered **HTTP 520**
when probed. The adapter refuses an unwired venue by name and lists what was tried, so nobody
repeats the search. **Warehouse receipts (仓单) and basis are not in this channel** — SHFE's stock
file was probed at five plausible paths and is at none of them, so it needs its own investigation
rather than a guessed URL. The L2 gap item therefore closes only partially.

**Member rankings: a third exchange wired, two proven unreachable.** CFFEX was added on the same
channel (`--venue CFFEX`), giving eight varieties (IF/IC/IH/IM/T/TF/TS/TL) at L2. Two traps, both
measuring rather than guessing: the filename is `{VARIETY}_1.csv` and **not** `{VARIETY}{YYMM}_1.csv`
(the latter only ever returns the same generic 2 KB error page), and the host serves **GBK** and
gates on a `Referer` — without either, that same error page comes back, so a wrong filename looks
like a parse failure rather than a 404. The adapter now detects the HTML page explicitly instead of
reading it as an empty ranking.

The two remaining venues are unreachable, and the reasons are measured: **DCE** returns HTTP 412 to
a scripted client even with a full browser header set (its homepage too), and its portal export
resets the connection; **GFEX** returns HTTP 520 on every request shape of its position interface
while its SPA homepage exposes no interface paths at all. This is not a local quirk — akshare has an
open issue for the same DCE 412, and its own inventory functions reach the exchanges only through
aggregators. Four of five venues are therefore wired; DCE and GFEX stay recorded as gaps.

**Warehouse receipts and basis: implemented at the only tier that is actually reachable.** This is
the honest version of the gap rather than a closed one. The official stock files are not obtainable:
SHFE's `dailystock` 404s on both its hosts and several path shapes (its working daily JSON carries
settlement prices but **no warehouse or spot fields**), CZCE publishes receipts only as an opaque
per-session `rootfiles` PDF whose URL cannot be derived, and DCE is blocked outright. So
`mira_data fetch futures_warehouse rb` reads the Eastmoney relay's `RPT_FUTU_STOCKDATA`
(`ON_WARRANT_NUM`, 73 varieties) and `mira_data fetch futures_basis rb` reads the licensed vendor's
`latest-basis`. Both are **L5 relays**, and each record says so along with the reason, so nothing
can be upgraded to L2 by assumption.

Three measured details keep the relay usable:

- the relay's variety casing is **inconsistent within one result set** (`RB`/`CU` uppercase beside
  `si`/`lc` lowercase) and an unmatched case returns an empty result rather than an error, which
  reads exactly like "no receipts" — so both forms are tried;
- the vendor's basis satisfies `spot - close == basis` (rb: 3260 - 3112 = 148). That identity is
  checked per row and a mismatch is **flagged in provenance** rather than published as if it
  reconciled;
- the basis figures are **vendor-published, not recomputed here**, so they are `reported_metric`
  rows carrying no ledger obligation, and provenance records that Mira did not derive them.

**Item 6 (commodity / FX / credit): partly already covered, and the rest is now wired.** The
honest finding first — **FRED already carries most of this gap's substance**, so the need was
discoverability rather than a new adapter. Probed live 2026-10-03 through the existing
`macro_fred` channel:

| what | FRED series | value read |
| --- | --- | --- |
| US high-yield credit spread | `BAMLH0A0HYM2` | 3.24 |
| USD/CNY | `DEXCHUS` | 6.711 |
| WTI crude | `DCOILWTICO` | 96.16 |
| broad dollar index | `DTWEXBGS` | 120.33 |
| 10y-2y Treasury spread | `T10Y2Y` | 0.45 |

Those series ids are now recorded in [macro-series-ids.md](../data/macro-series-ids.md), because an
unlisted id is indistinguishable from an unsupported one. What FRED does **not** carry is the Treasury's own
debt and average-interest statistics and the CFTC's positioning report, and those are two
genuinely different sources rather than more series:

- **`treasury_debt` / `treasury_avg_interest`** read the Treasury's own public API (keyless):
  total public debt outstanding 40,260,641,972,390.03 for 2026-10-01, and 16 average-interest
  rows across bills, notes, bonds, TIPS, FRNs and the Federal Financing Bank. Two contract
  details: amounts arrive as **strings** including decimals, and `avg_interest_rates` **mixes
  instruments**, so each security description is claimed separately rather than one "latest"
  row. The Treasury's exchange-rate dataset is published on the same API but a live probe failed
  at the network layer, so it is refused by name rather than guessed at.
- **`cftc_cot`** reads the CFTC's weekly Commitments of Traders report from its Socrata endpoint
  (keyless). Four datasets were probed and each returned rows; an initial label guess was
  **wrong** — `6dca-aqww` serves WHEAT-SRW, so it is the *legacy* report covering commodity and
  financial markets, not a "financial futures" report, and the labels now say what each dataset
  actually returns.

The trap worth keeping from this item: **a substring market filter is not a unique key.** `GOLD`
matches both COMEX gold (open interest 406,456) and a Coinbase PAX-Gold perpetual (1,512), and
the API's default ordering is alphabetical — so the minor market was being surfaced as "the"
gold position. Rows are now ordered by report date then open interest descending, which puts the
economically significant market first while the rest stay visible in the series.

**Deliberately absent.** No news/media channel yet, because the 11 canonical families have no
media shape and adding one is a protocol change under review (§10). 龙虎榜 / 大宗 / 北向 flows
route through aggregators and would land at L5 if built, and northbound **net flow is dead
upstream** (verified nulls), so only holdings are obtainable. Sell-side report text stays
manual/licensed; only its consensus numbers are ingested.

## 9. Cheap Cleanups (worth doing regardless)

- ~~Re-annotate `stooq_history_csv_endpoint` (JS-PoW wall; demote to optional/manual).~~ **Done (P1).**
- Quarantine or purge dated, ticker-specific registry rows.
- Fix the `twse` L2/L5, blog-L1, and transcript L1/L4 tier inconsistencies.
- Document the single freshness-field map.
- `scripts/validate_repo.py` globs `**/evidence-log.csv` and so scans gitignored
  `private/` bundles. They now validate clean, but the validator should skip
  `private/` (and other gitignored paths) so scratch fetches never enter CI scope.

## 10. Open Questions

- FRED/BEA key management: resolved by the local config file (§8b) —
  `FRED_API_KEY` / `BEA_API_KEY` in `private/mira-data.env`. The FRED/BEA
  *adapters* are still deferred (BLS keyless ships first).
- Non-US fact-grade fundamentals: out of scope for P1 (yfinance screening only; `source_gap` for
  fact-grade). Revisit with exchange/OpenDART adapters in P4.
- Exact capability-field vs overlay shape (§5) needs a `vocab.json` + `routing.schema.json` change
  that must pass `just check`; finalize wording in P2/P3 wiring.
- **A-share news/media channel: blocked on a family decision.** The 11 canonical families have no
  media shape (`transcript_claim` is a transcript, `estimate_revision` is a revision of estimates),
  so 个股新闻 needs a new family — proposed as `media_report`, following the `issuer_disclosure`
  precedent of adding a family rather than forcing an existing one. Until that is decided, media
  coverage stays manual, and the A-share layer is complete everywhere else (§8d).
- **Research-package orchestration entry is the next phase, not yet started.** The channels above
  are per-name, per-family reads, so a single-name study still means eight commands and eight
  separate evidence logs, while `loops/research-loop.md` expects one `investment-memo.md` plus one
  merged `evidence-log.csv`. The entry point would fan out across the layers for one name, merge
  the records into a single claim chain (carrying each claim's tier and any L1-vs-L5 conflict),
  and write a package directory with `must_refresh_if` and source gaps. Scope and shape are the
  user's call.
