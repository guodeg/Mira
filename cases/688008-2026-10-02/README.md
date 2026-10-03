# 688008 澜起科技 (Montage Technology) 2026-10 Case

Single-name A-share (STAR board) research package for 澜起科技 / Montage Technology,
688008.SH with its 06809.HK H-share line. Built 2026-10-02 from the CNINFO primary
filing chain plus vendor financials and market data, with every key figure recomputed.

## Open Source Notice

- case_status: research_package
- not_investment_advice: true
- stale_after: 2026-10-31
- refresh_policy: refresh before any live trading or portfolio decision
- readiness_level: watch_only
- readiness_basis: full primary-source chain with recomputed figures, capped by an
  irreducible legal uncertainty (Korean antitrust investigation) and the absence of any
  independent third-party industry source

## Case Metadata

- company: 澜起科技 Montage Technology
- tickers: 688008.SH (A, STAR board), 06809.HK (H)
- market: CN_A_SHARE_STAR
- research_cutoff_date: 2026-10-02
- market_data_through: 2026-09-30
- financial_data_through: 2026H1
- selected_overlays: valuation-expectation, earnings-quality
- primary_loop: loops/research-loop.md

## Artifacts

| file | role |
| --- | --- |
| `investment-memo.md` | hero artifact: the research conclusion |
| `evidence-log.csv` | canonical v1.2 claim log (58 claims) |
| `case-notes.md` | working notes, cross-checks and open items |
| `research-package-manifest.json` | package manifest (`mira_research_package_v1`) |
| `cninfo_titles.txt` | the 152-announcement CNINFO index this case was built from |
| `final_metrics.py`, `metrics.py`, `tech.py`, `relative.py`, `parse.py`, `trace.py` | reproducible calculation artifacts behind the ledgered numbers |

## Untracked local payloads

These are present on the machine that built the case but deliberately not tracked:

- `raw/` — vendor (hithink-finance) JSON payloads; licence terms unverified, kept private
- `pdftext/`, `pdftext_via_toolkit/` — extracted text of issuer filings, third-party
  copyright; re-derivable from CNINFO via `mira_data cninfo text <code> --title <substring>`
  (needs the optional `pypdf` dependency)
- `_pylibs/` — a vendored copy of `pypdf`, not repo content

## Refresh Triggers

See `must_refresh_if` in the manifest. The dominant ones: any development in the Korean
antitrust investigation, 2026Q3 segment gross margin and 扣非 growth, buyback execution
against the RMB 600m cap, and price outside 177 / 233.
