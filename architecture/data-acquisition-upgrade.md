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
**96 rows, 20 of them `public_api`**; `tools/mira_data/` ships **22 fetch families, 14 usable
with zero optional dependencies** (the other 8 are gated on `futu-api` / `ib_insync`); and
**12 offline suites** run inside `scripts/run_quality_gate.py`. §8c documents the US
channels; this section documents the mainland ones, which have their own traps and their own
tier split.

| layer | family | adapter / registered source | tier | command |
| --- | --- | --- | --- | --- |
| L1 | issuer_disclosure | CNINFO 公告索引 (`cninfo_announcement_api`) | issuer_primary_disclosure | `mira_data fetch cninfo_announcements 600519 --since 2026-07-01` |
| L2 | macro_series | NBS 数据发布库, 13 series (`nbs_stats_api`) | fact | `mira_data fetch macro_nbs ALL` |
| L2 | macro_series | CFETS LPR / Shibor (`chinamoney_rates_api`) | fact | `mira_data fetch macro_rates BOTH` |
| L2 | ownership_short_interest | SSE / SZSE margin (`sse_margin_api`, `szse_margin_api`) | fact | `mira_data fetch margin_balance 600519` |
| L2 | transcript_claim | 互动易 / 上证e互动 (`irm_cninfo_api`, `sse_einteraction_api`) | company_claim | `mira_data fetch investor_qa 000001` |
| L3 | consensus_estimate | Eastmoney 盈利预测 (`eastmoney_consensus_api`) | forecast | `mira_data fetch consensus_estimate 600519` |
| L5 | market_price, company_financials | licensed hithink-finance CLI (`hithink_finance_api`) | market_pricing / reported_metric | `mira_data fetch hithink_market_price 600519` |
| L5 | options_surface | ETF options over the same CLI (`hithink_finance_api`) | market_pricing | `mira_data fetch option_surface 510050` |
| L5 | macro_series | Eastmoney national-accounts relay (`eastmoney_macro_api`) | reported_metric | `mira_data fetch macro_china ALL` |

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
