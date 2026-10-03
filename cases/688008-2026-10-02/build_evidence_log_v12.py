#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rewrite evidence-log.csv from the legacy 13-col shape into the canonical v1.2
22-col shape.  Pure mechanical mapping of the existing rows -- no new facts.

Run:  python _build_evidence_log_v12.py
"""
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

CASE = Path(__file__).resolve().parent

HEADER = [
    "source_id", "claim_area", "claim_type", "claim_text", "source_speaker",
    "verification_status", "authority_level", "source_date", "as_of_date",
    "url_or_path", "used_by_agent", "used_by_skill", "confidence",
    "upstream_sources", "notes", "evidence_category", "freshness_status",
    "conflict_status", "treatment", "readiness_impact", "source_language",
    "translation_basis",
]

AGENT = "research-orchestrator"
SKILL = "equity-research-core"
NA = "not_applicable"

# bilingual source-language path (Chinese source, English claim_text)
ZH_T = ("zh-CN", "mira_translation")
# Mira-derived row with no source text of its own
MIRA_LANG = (NA, NA)


def r(source_id, claim_area, claim_type, claim_text, speaker, vstatus, level,
      sdate, adate, path, conf, upstream, notes, category, fresh, conflict,
      treat, impact, lang=ZH_T):
    return {
        "source_id": source_id, "claim_area": claim_area, "claim_type": claim_type,
        "claim_text": claim_text, "source_speaker": speaker,
        "verification_status": vstatus, "authority_level": level,
        "source_date": sdate, "as_of_date": adate, "url_or_path": path,
        "used_by_agent": AGENT, "used_by_skill": SKILL, "confidence": conf,
        "upstream_sources": upstream, "notes": notes, "evidence_category": category,
        "freshness_status": fresh, "conflict_status": conflict,
        "treatment": treat, "readiness_impact": impact,
        "source_language": lang[0], "translation_basis": lang[1],
    }


ROWS = [
    r("montage_688008_p01", "market_data", "fact",
      "2026-09-30 close 202.31 CNY, -4.84% d/d, volume 26,524,480 shares, turnover 54.34亿元; "
      "2026-10-01/02 were National Day holidays so this is the latest trade print as of the research date.",
      "market", "verified", "L5", "2026-09-30", "2026-09-30",
      "raw/snapshot.json, raw/history_raw.json", "high", NA,
      "source=hithink-finance market.snapshot + market.history (vendor_aggregator, L5); "
      "last trade date 2026-09-30, exchange closed 2026-10-01/02 for National Day; "
      "not a fundamental verification, market data only.",
      "verified_fact", "current", "none", "use_normally",
      "supports_durable_conclusion"),

    r("montage_688008_p02", "ownership", "reported_metric",
      "Post-greenshoe total share capital = 1,222,200,021 shares; pre-IPO A-share capital 1,146,426,521; "
      "H-share issue 65,890,000 plus 9,883,500 greenshoe shares.",
      "company", "verified", "L1", "2026-02-10", "2026-02-10",
      "pdftext/2026-02-10_...H股挂牌并上市交易的公告.txt", "high", NA,
      "source=澜起科技 关于H股挂牌并上市交易的公告 (公告编号2026-010) plus 关于悉数行使超额配售权的公告; "
      "Formula: 1,146,426,521 + 65,890,000 + 9,883,500 = 1,222,200,021; "
      "filing in body states listing 2026-02-09 and full greenshoe exercise 2026-02-11, "
      "source_date uses the 2026-02-10 announcement date carried in the row; share count is stated capital, "
      "not an EPS-implied estimate.",
      "verified_fact", "current", "none", "use_normally",
      "supports_durable_conclusion"),

    r("montage_688008_p03", "ownership", "reported_metric",
      "2026-06-30 total share capital 1,220,538,021 of which 11,781,000 were held in the buyback special "
      "account, so the dividend record-date base is 1,208,757,021 shares.",
      "company", "verified", "L1", "2026-08-29", "2026-08-29",
      "pdftext/2026-08-29_...中期利润分配方案公告.txt", "high", NA,
      "source=2026年半年度报告 摘要 §1.6 plus 2026年中期利润分配方案公告; "
      "Formula: 1,220,538,021 - 11,781,000 = 1,208,757,021; as_of_date is the 2026-06-30 balance-sheet date, "
      "source_date the 2026-08-29 publication date.",
      "verified_fact", "current", "none", "use_normally",
      "supports_durable_conclusion"),

    r("montage_688008_p04", "valuation", "derived_calculation",
      "Corrected total market cap at a 202.31 CNY close = 2,472.6亿元; cross-checks against the served "
      "vendor values PB 11.49x vs 11.476 and PE_MRQ 61.90x vs 61.813.",
      "mira", "modeled", "L6", "2026-09-30", "2026-09-30",
      "final_metrics.py §A; raw/valuation.json", "high", "montage_688008_p01",
      "source=Mira calculation validated against hithink-finance valuation.snapshot; "
      "Formula: market cap = 1,222,200,021 shares (P02) x 202.31 CNY (P01) = 2,472.6亿元; "
      "cross-check=served PB 11.476 -> 11.49x and served PE_MRQ 61.813 -> 61.90x, both within rounding; "
      "vendor cross-check does not upgrade the row above modeled.",
      "estimate", "current", "none", "use_normally",
      "supports_durable_conclusion", MIRA_LANG),

    r("montage_688008_p05", "financials", "reported_metric",
      "2026H1 revenue 33.35亿元 (+26.66%), 归母净利 19.97亿元 (+72.33%), 扣非归母 13.22亿元 (+21.17%), "
      "basic EPS 1.69, 扣非EPS 1.12 (+16.67%).",
      "company", "verified", "L1", "2026-08-29", "2026-08-29",
      "pdftext/2026-08-29_...半年度报告.txt", "high", NA,
      "source=2026年半年度报告 §二(二) 主要财务指标; as_of_date 2026-06-30 period end, "
      "source_date 2026-08-29 publication; 扣非 = ex-non-recurring net profit attributable to parent.",
      "verified_fact", "current", "none", "use_normally",
      "supports_durable_conclusion"),

    r("montage_688008_p06", "financials", "reported_metric",
      "2026H1 非经常性损益合计 6.749亿元 versus only 0.68亿元 in 2025H1; components: financial-asset "
      "fair-value change and disposal gains 6.873亿元, government grants 0.058亿元, structured deposits "
      "and similar 0.060亿元, less income-tax effect 0.237亿元 and minority interests 0.004亿元.",
      "company", "verified", "L1", "2026-08-29", "2026-08-29",
      "pdftext/2026-08-29_...半年度报告.txt L491-511", "high", NA,
      "source=2026年半年度报告 §八 非经常性损益项目和金额; unit basis RMB; "
      "components do not sum exactly to the 6.749亿元 total in the disclosure's own presentation; "
      "carried as reported (used as input to P07).",
      "verified_fact", "current", "none", "use_normally",
      "supports_durable_conclusion"),

    r("montage_688008_p07", "accounting_quality", "derived_calculation",
      "Non-recurring items were 34% of 2026H1 归母净利 (6.75/19.97) versus 5.9% in 2025H1 (0.68/11.59), so "
      "the headline +72.3% growth materially overstates core momentum.",
      "mira", "modeled", "L6", "2026-09-30", "2026-09-30",
      "final_metrics.py §C", "high",
      "montage_688008_p05;montage_688008_p06",
      "source=Mira calculation from P05+P06; "
      "Formula: non-recurring share = 6.749 / 19.97 = 33.8% (2026H1); 0.68 / 11.59 = 5.9% (2025H1); "
      "2025H1 归母净利 11.59亿元 is the prior-year comparative used in the same filing section; "
      "interpretation is arithmetic only, the 'overstates' wording is the judgment layer.",
      "estimate", "current", "none", "use_normally",
      "supports_durable_conclusion", MIRA_LANG),

    r("montage_688008_p08", "financials", "reported_metric",
      "2026H1 投资收益及公允价值变动收益合计 6.82亿元, +5,939.4% yoy; main drivers were disposal gains on "
      "other non-current financial assets and fair-value gains on investee companies.",
      "company", "verified", "L1", "2026-08-29", "2026-08-29",
      "pdftext/2026-08-29_...半年度报告.txt L486, L2084-2085, L3717, L6688", "high", NA,
      "source=2026年半年度报告 §三(一) 及 附注(七)68/70; the +5,939.4% base is a near-zero 2025H1 "
      "comparative, so the percentage is not a meaningful growth rate; unit basis RMB.",
      "verified_fact", "current", "none", "use_normally",
      "supports_durable_conclusion"),

    r("montage_688008_p09", "financials", "reported_metric",
      "2026H1 FX loss 1.74亿元 versus 0.07亿元 in 2025H1, driven by RMB appreciation against USD on "
      "foreign-currency assets, principally the H-share proceeds; FY2025 FX loss was 0.4075亿元.",
      "company", "verified", "L1", "2026-08-29", "2026-08-29",
      "pdftext/2026-08-29_...半年度报告.txt L1269-1271; 2025年报 L2275", "high", NA,
      "source=2026年半年度报告 §三(一) plus 2025年年度报告 风险因素; two filings share this row, "
      "source_date is the 2026H1 report (the fresher input).",
      "verified_fact", "current", "none", "use_normally",
      "supports_durable_conclusion"),

    r("montage_688008_p10", "financials", "reported_metric",
      "2026H1 股份支付费用 1.91亿元, charged through recurring P&L with a 1.827亿元 after-tax effect; "
      "excluding share-based payment, 归母净利 21.80亿元 (+63.6%) and 扣非归母 15.05亿元 (+19.0%).",
      "company", "verified", "L1", "2026-08-29", "2026-08-29",
      "pdftext/2026-08-29_...半年度报告.txt L519-536, L1274-1278", "high", NA,
      "source=2026年半年度报告 §二(九)(十), §三(一); unit basis RMB; the ex-SBP figures are the "
      "company's own adjusted presentation, not a Mira derivation.",
      "verified_fact", "current", "none", "use_normally",
      "supports_durable_conclusion"),

    r("montage_688008_p11", "segments", "reported_metric",
      "2026H1 segment detail: 互连类芯片 revenue 31.11亿元, cost 9.55亿元, GM 69.3% (99.0% of gross "
      "profit); 津逮产品 revenue 2.20亿元, cost 1.99亿元, GM 9.7% (1.0% of gross profit); 租赁 0.042亿元 "
      "at GM 30.5%.",
      "company", "verified", "L1", "2026-08-29", "2026-06-30",
      "pdftext/2026-08-29_...半年度报告.txt L6578-6585", "high", NA,
      "source=2026年半年度报告 附注 营业收入/营业成本分解; this is the primary segment disclosure and the "
      "input for the P12 mix explanation; as_of_date 2026-06-30 period end.",
      "verified_fact", "current", "none", "use_normally",
      "supports_durable_conclusion"),

    r("montage_688008_p12", "segments", "derived_calculation",
      "津逮 single-quarter revenue 0.42亿元 in 2026Q1 -> 1.78亿元 in 2026Q2 (+324% qoq, derived); this mix "
      "shift, not pricing, explains the Q1->Q2 total gross-margin fall from 69.79% to 61.83%.",
      "mira", "modeled", "L6", "2026-09-30", "2026-09-30",
      "final_metrics.py §D", "medium", "montage_688008_p11",
      "source=Mira calculation, 津逮 Q1 figure from company segment disclosure (P11); "
      "Formula: qoq growth = 1.78 / 0.42 - 1 = +324%; GM attribution uses segment revenue x segment GM "
      "weights from P11; medium confidence because the Q1 single-quarter split is derived by differencing "
      "cumulative filings.",
      "estimate", "current", "none", "use_normally",
      "supports_durable_conclusion", MIRA_LANG),

    r("montage_688008_p13", "segments", "reported_metric",
      "2026H1 geography: 境内(含香港) revenue 10.11亿元 at 53.3% GM; 境外 revenue 23.20亿元 at 70.6% GM, "
      "i.e. 69.6% of revenue.",
      "company", "verified", "L1", "2026-08-29", "2026-06-30",
      "pdftext/2026-08-29_...半年度报告.txt L6581-6582", "high", NA,
      "source=2026年半年度报告 附注 营业收入分解; 境内 includes Hong Kong; as_of_date 2026-06-30 period end.",
      "verified_fact", "current", "none", "use_normally",
      "supports_durable_conclusion"),

    r("montage_688008_p14", "financials", "reported_metric",
      "2026Q2 single quarter: revenue 18.75亿元 (+32.8% yoy, +28.3% qoq), 归母净利 11.50亿元 (+81.5% yoy, "
      "+35.7% qoq), ex-share-based-payment 归母 12.38亿元 (+69.2%), 互连类 revenue 16.94亿元 at 67.4% GM.",
      "company", "verified", "L1", "2026-08-29", "2026-08-29",
      "pdftext/2026-08-29_...半年度报告.txt L1279-1286", "high", NA,
      "source=2026年半年度报告 §三(一)2; company-published single-quarter figures (contrast G08, where "
      "Mira had to difference cumulative YTD for the total-GM line).",
      "verified_fact", "current", "none", "use_normally",
      "supports_durable_conclusion"),

    r("montage_688008_p15", "financials", "reported_metric",
      "FY2025: revenue 54.56亿元 (+49.94%), 营业利润 23.22亿元 (+64.33%), 归母净利 22.36亿元 (+58.35%), "
      "扣非归母 20.22亿元 (+61.95%), weighted ROE 18.25%, basic EPS 1.97.",
      "company", "verified", "L1", "2026-02-28", "2026-02-28",
      "pdftext/2026-02-28_...业绩快报公告.txt", "high", NA,
      "source=澜起科技2025年度业绩快报公告 (2026-013); express-report (业绩快报) figures, later superseded "
      "by the audited annual report, hence freshness=acceptable_for_period; unit basis RMB.",
      "verified_fact", "acceptable_for_period", "none", "use_normally",
      "supports_durable_conclusion"),

    r("montage_688008_p16", "segments", "reported_metric",
      "FY2025 segment detail: 互连类芯片 51.39亿元 (+53.4%) at 65.6% GM; 津逮产品线 3.08亿元 (+10.25%); "
      "FY2025 股份支付费用 4.31亿元 with a 4.12亿元 after-tax effect, ex-SBP 归母净利 26.47亿元 (+80.98%).",
      "company", "verified", "L1", "2026-02-28", "2026-02-28",
      "pdftext/2026-02-28_...业绩快报公告.txt; 2025年报", "high", NA,
      "source=2025年度业绩快报 plus 2025年年度报告 L1397-1424; two filings share this row; "
      "express-report basis, superseded by the audited annual report -> acceptable_for_period.",
      "verified_fact", "acceptable_for_period", "none", "use_normally",
      "supports_durable_conclusion"),

    r("montage_688008_p17", "regulatory", "reported_metric",
      "On 2026-07-15 the Seoul Central District Prosecutors' Office Fair Trade Investigation Division "
      "conducted an on-site search and evidence seizure at Montage's Korea office over a potential "
      "antitrust violation; no charges were filed against the company, directors or employees as of the "
      "disclosure date, and the timing and outcome are not predictable.",
      "company", "verified", "L1", "2026-07-17", "2026-07-17",
      "pdftext/2026-07-17_...配合韩国相关调查的说明公告.txt", "high", NA,
      "source=澜起科技 关于配合韩国相关调查的说明公告 (公告编号2026-040); company filing states the raid on "
      "its own premises only and uses the neutral phrase 潜在的违反反垄断相关法规; "
      "scope caveat=accurate but incomplete on the three-issuer scope and the price-fixing characterization "
      "(see P17b/P17c/P17d); scope_status=contradicted_by_p17b.",
      "verified_fact", "current", "none", "sensitize",
      "blocks_actionability"),

    r("montage_688008_p17b", "regulatory", "fact",
      "The 2026-07-15 raid was not Montage-specific: Yonhap reports the same prosecutors' division raided "
      "the Korean offices of all three global memory-interface suppliers on the same day -- Montage "
      "Technology (China), Renesas Electronics (Japan) and Rambus (US) -- on suspicion of violating the "
      "Fair Trade Act by colluding on prices (담합) when supplying components to Samsung Electronics, "
      "SK hynix and Micron; prosecutors seized mobile phones from officials of some of the companies; "
      "case lead is chief prosecutor Na Hee-seok (나희석).",
      "media", "verified", "L4", "2026-07-15", "2026-07-15",
      "raw/korea_cartel_yonhap.txt", "high", NA,
      "source=연합뉴스 Yonhap News https://www.yna.co.kr/view/AKR20260715163500004, published 2026-07-15 "
      "17:37 KST; scope correction to P17 -- the raid covered all three global MIC suppliers, not Montage "
      "alone; independent of the issuer; this is the row that corrects the scope error.",
      "verified_fact", "current", "none", "use_normally",
      "blocks_actionability", ("ko", "mira_translation")),

    r("montage_688008_p17c", "regulatory", "fact",
      "Seoul Economic Daily independently confirms P17b in English under the headline 'Prosecutors Raid "
      "Three Global Chip Suppliers Over Price-Fixing', names the product as memory interface chips (MIC) "
      "and repeats the Samsung / SK hynix / Micron customer set.",
      "media", "verified", "L4", "2026-07-15", "2026-07-15",
      "raw/korea_cartel_sedaily.txt", "high", NA,
      "source=Seoul Economic Daily (English edition) "
      "https://en.sedaily.com/society/2026/07/15/prosecutors-raid-three-global-chip-suppliers-over-price; "
      "provenance caveat=the row is carried with source_language=en because the cited edition is "
      "en.sedaily.com, but the referenced local file raw/korea_cartel_sedaily.txt is NOT present in this "
      "case dir (only raw/korea_cartel_yonhap.txt is), so the English text could not be re-verified here.",
      "verified_fact", "current", "none", "use_normally",
      "blocks_actionability", ("en", "not_translated")),

    r("montage_688008_p17d", "regulatory", "derived_calculation",
      "The company's own disclosure (P17) names only its own Korea office and uses the neutral phrase "
      "'潜在的违反反垄断相关法规'; it does not disclose that Renesas and Rambus were raided the same day, "
      "nor that the alleged conduct is price-fixing under the Fair Trade Act -- the filing is therefore "
      "accurate but materially incomplete on scope.",
      "mira", "modeled", "L6", "2026-10-02", "2026-10-02",
      "investment-memo.md §2.7", "high",
      "montage_688008_p17;montage_688008_p17b;montage_688008_p17c",
      "source=Mira comparison of the company filing (P17) against wire reporting (P17b/P17c); "
      "Formula: scope_delta = {3 issuers raided, price-fixing alleged} (P17b/P17c) MINUS "
      "{1 issuer, neutral 'potential antitrust violation' wording} (P17); items present in the wire "
      "reporting and absent from the filing constitute the undisclosed scope.",
      "inference", "current", "none", "sensitize",
      "blocks_actionability", MIRA_LANG),

    r("montage_688008_p17e", "risk", "derived_calculation",
      "Prior-probability read: a criminal-referral cartel probe naming all three members of a CR3>90% "
      "oligopoly on the same day points to alleged coordinated conduct rather than one firm's misconduct; "
      "this makes the risk a threat to the source of the 69.3% interconnect gross margin (behavioural "
      "remedies and customer supply diversification) rather than merely a fine, and gives the high, "
      "rising, never-price-warred oligopoly margins an alternative hypothesis not considered in the "
      "first draft.",
      "mira", "modeled", "L6", "2026-10-02", "2026-10-02",
      "investment-memo.md §2.7, J3", "medium",
      "montage_688008_p17b;montage_688008_p17c;montage_688008_p11;montage_688008_p16",
      "source=Mira inference from P17b/P17c plus the margin series in P11 (2026H1 互连类 GM 69.3%) and "
      "P16 (FY2025 互连类 GM 65.6%); Formula: margin_source_risk = f(CR3>90% coordinated-conduct "
      "probability, share of gross profit from 互连类 = 99.0% per P11) -> behavioural-remedy and "
      "customer-deconcentration channel, not fine quantum; margin history has no dedicated evidence row "
      "of its own beyond P11/P16; medium confidence, this is a judgment not a measurement.",
      "inference", "current", "none", "sensitize",
      "blocks_actionability", MIRA_LANG),

    r("montage_688008_p17f", "regulatory", "interpretation",
      "Counterweight that must travel with P17b/P17e: this remains an allegation ('혐의'), not a finding; "
      "no company or individual has been charged; Korean antitrust matters usually end in KFTC fines and "
      "a direct criminal referral is less common; even a worst-case fine is not an existential threat "
      "given net cash of 133.7亿元 -- the real damage channel is behavioural remedies and customer "
      "de-concentration, not the penalty.",
      "mira", "modeled", "L6", "2026-10-02", "2026-10-02",
      "investment-memo.md §2.7", "medium",
      "montage_688008_p17b;montage_688008_p17e",
      "source=Mira judgement (L6 interpretation, not a derived number); net cash 133.7亿元 corroborates "
      "the memo's balance-sheet line (cash 151.10亿元 - total liabilities 17.40亿元 = 133.69亿元, per the "
      "2026年半年度报告) -- no dedicated evidence row exists for that balance-sheet figure in this log; "
      "no upstream quantitative row, basis is the wire reports plus P17e.",
      "inference", "current", "none", "sensitize",
      "supports_working_view", MIRA_LANG),

    r("montage_688008_p17g", "source_gap", "interpretation",
      "Reporting on the cartel probe was not obtainable in the first pass because web_search was broken "
      "at the harness level, so the scope error (P17 vs P17b) persisted until the endpoint was fixed and "
      "the wire report was retrieved.",
      "mira", "verified", "L6", "2026-10-02", "2026-10-02",
      "case-notes.md", "high", "montage_688008_p17;montage_688008_p17b",
      "source=Mira environment note; the harness failure is directly observable in this session "
      "(web_search endpoint HTTP 404, web_fetch blocked by the SSRF guard on a fake-IP proxy range); "
      "process gap, not a market or company fact; blocks_publication until the wire-sourced scope "
      "correction is carried in the published package.",
      "verified_fact", "current", "none", "open_item",
      "blocks_publication", MIRA_LANG),

    r("montage_688008_p18", "regulatory", "reported_metric",
      "The 2026 Korean raid was disclosed on 2026-07-17 but was not flagged in the parallel news-research "
      "stream, which asserted 'no regulatory inquiry/litigation/penalty' in 2026 -- an assertion "
      "contradicted by the primary filing.",
      "mira", "verified", "L1", "2026-10-02", "2026-10-02",
      "pdftext/2026-07-17_...配合韩国相关调查的说明公告.txt", "high", NA,
      "source=Mira cross-check of the primary filing against the subagent research summary; underlying "
      "content is the company's own 2026-07-17 disclosure (L1), so source_date here is the cross-check "
      "date 2026-10-02 while the contradicted disclosure itself is dated 2026-07-17; treatment=exclude, "
      "the contradicted news-stream assertion is kept out of the conclusion chain.",
      "contradicted", "current", "contradicted", "exclude",
      "blocks_actionability"),

    r("montage_688008_p19", "capital_allocation", "reported_metric",
      "H-share listing: 06809.HK, offer price HK$106.89, 65,890,000 shares, listed on HKEX Main Board "
      "2026-02-09; net proceeds before greenshoe ~HK$69.05亿元; greenshoe of 9,883,500 shares fully "
      "exercised 2026-02-11.",
      "company", "verified", "L1", "2026-02-11", "2026-02-10",
      "raw/cninfo_announcements.json; pdftext/2026-02-10_...", "high", NA,
      "source=关于H股公开发行价格的公告, 关于H股挂牌并上市交易的公告, 关于悉数行使超额配售权的公告; "
      "unit label as carried in the row (~HK$69.05亿元 mixes HKD and the 亿元 scale); source_date set to "
      "the last event in the row (full greenshoe exercise 2026-02-11); as_of_date kept at the original "
      "2026-02-10 announcement date.",
      "verified_fact", "current", "none", "use_normally",
      "supports_durable_conclusion"),

    r("montage_688008_p20", "financials", "derived_calculation",
      "The ~79亿元 one-quarter equity increase in 2026Q1 is explained by the H-share IPO: board equity "
      "128.71亿元 (2025-12) -> 207.75亿元 (2026-03), H1 2026 financing cash inflow +72.22亿元, and the H1 "
      "report itself attributes the 66.5% net-asset growth to '主要是由于公司发行H股收到募集资金所致'.",
      "mira", "modeled", "L6", "2026-06-30", "2026-06-30",
      "pdftext/2026-08-29_...半年度报告.txt L487-488; final_metrics.py", "high",
      "montage_688008_p19",
      "source=2026年半年度报告 §二(二)说明 combined with the balance-sheet and cash-flow series from the "
      "vendor quarterly data; Formula: equity delta = 207.75 - 128.71 = +79.04亿元 (2026Q1); "
      "financing inflow +72.22亿元 (2026H1); the vendor quarterly balance-sheet/cash-flow series has no "
      "standalone evidence row of its own in this log, P19 supplies the IPO proceeds leg; "
      "compound-row note=the original carried a compound label (company_filing + derived); primary content "
      "is the Mira derivation, with the company's own attribution statement quoted inside.",
      "inference", "current", "none", "use_normally",
      "supports_durable_conclusion", MIRA_LANG),

    r("montage_688008_p21", "capital_allocation", "reported_metric",
      "XConn disposal: Montage's wholly-owned Cayman subsidiary held 13.075% of XConn Technologies; "
      "Marvell agreed to acquire XConn for a base total consideration of US$540m; Montage's share is "
      "expected to be US$58-65m against a ~US$8.97m initial cost (152-182% premium over 2025-09-30 book "
      "value); consideration 60% cash / 40% Marvell stock at US$88.3554.",
      "company", "verified", "L1", "2026-01-07", "2026-01-07",
      "pdftext/2026-01-07_...出售资产的公告.txt", "high", NA,
      "source=澜起科技 关于出售资产的公告 (公告编号2026-003); unit basis USD; the 152-182% premium range "
      "is the row's own stated range; expected proceeds are forward-looking within a signed transaction; "
      "readiness_note=supports_durable_conclusion, downgrade control is that proceeds sit in the filing "
      "and are not yet cash-received.",
      "verified_fact", "current", "none", "use_normally",
      "supports_durable_conclusion"),

    r("montage_688008_p22", "capital_allocation", "reported_metric",
      "Buyback: an RMB 300-600m programme authorised with a price cap of RMB 332.90; as of 2026-09-30 "
      "2,088,000 shares (0.17%) had been repurchased for RMB 410.2m at RMB 184.14-210.00; the window "
      "runs to 2026-10-23.",
      "company", "disclosed", "L1", "2026-10-01", "2026-10-01",
      "raw/cninfo_announcements.json (2026-07-24, 2026-07-30, 2026-10-01)", "medium", NA,
      "source=关于以集中竞价交易方式回购A股股份的回购报告书 plus 回购进展公告; provenance caveat=the "
      "progress figures arrive via the CNINFO announcement index (third-party aggregation of company "
      "filings), hence disclosed rather than verified; source_date 2026-10-01 is the announcement date "
      "carried in the row and is not independently re-checked (it falls on the National Day holiday).",
      "reported_fact", "current", "none", "attribute",
      "supports_working_view"),

    r("montage_688008_p23", "ownership", "reported_metric",
      "Shareholder reduction: on 2026-09-25 WLT Partners planned to sell up to 2,330,000 shares (0.19%) "
      "between 2026-11-02 and 2027-02-01; an earlier 2026-05-28 询价转让 placed 12,228,000 shares "
      "(1.00%) at RMB 250.08; an even earlier 上海融迎 sale covered 11,451,451 shares at RMB "
      "112.79-155.52.",
      "company", "verified", "L1", "2026-09-25", "2026-09-25",
      "raw/cninfo_announcements.json (2026-09-25, 2026-05-29, 2025-09-11, 2026-01-10)", "high", NA,
      "source=股东减持股份计划公告, 股东询价转让结果报告书, 集中竞价减持结果公告; announcement dates "
      "span the row (2025-09-11 to 2026-09-25), source_date uses the latest and most material item (the "
      "2026-09-25 reduction plan); the plan itself is forward-looking and only partially executed.",
      "verified_fact", "current", "none", "use_normally",
      "supports_working_view"),

    r("montage_688008_p24", "ownership", "reported_metric",
      "Shareholder structure at 2026-06-30: the company has no controlling shareholder and no actual "
      "controller; largest holders are 香港中央结算(陆股通) 11.93%, HKSCC NOMINEES (H shares) 6.20%, WLT "
      "Partners 3.68%, 中国电子投资控股 (state legal person) 2.43% and 上海融迎 2.10%; several STAR / "
      "semiconductor / AI index ETFs sit in the top 10; 260,865 shareholders in total.",
      "company", "verified", "L1", "2026-08-29", "2026-06-30",
      "pdftext/2026-08-29_...半年度报告摘要.txt L88-138", "high", NA,
      "source=2026年半年度报告 摘要 §2.3; as_of_date 2026-06-30 record date, source_date 2026-08-29 "
      "publication; absence of a controlling shareholder is a filing-level fact, not an inference.",
      "verified_fact", "current", "none", "use_normally",
      "supports_durable_conclusion"),

    r("montage_688008_p25", "capital_allocation", "reported_metric",
      "2026 interim dividend RMB 0.20 per share (每10股派2.00元), totalling RMB 241,751,404.20, i.e. "
      "12.10% of 2026H1 归母净利; no stock dividend and no capitalisation of reserves.",
      "company", "verified", "L1", "2026-08-29", "2026-08-29",
      "pdftext/2026-08-29_...中期利润分配方案公告.txt", "high", NA,
      "source=2026年中期利润分配方案公告 (2026-049) plus 2026年半年度报告摘要 §1.6; unit basis RMB.",
      "verified_fact", "current", "none", "use_normally",
      "supports_durable_conclusion"),

    r("montage_688008_p26", "competitive_position", "company_claim",
      "DDR5 roadmap: in 2026H1 the third- and fourth-generation RCD together passed 50% of shipments and "
      "the fifth generation began volume shipment; the sixth-generation RCD (9200 MT/s) was sampled to "
      "customers in June 2026; pre-research has started on the first-generation DDR6 memory interconnect "
      "product.",
      "company", "verified", "L1", "2026-08-29", "2026-08-29",
      "pdftext/2026-08-29_...半年度报告.txt L1288-1298", "high", NA,
      "source=2026年半年度报告 §三(二)1; original claim text mixes English and untranslated Chinese "
      "product terms (第三+第四子代 RCD, 第五子代, 第六子代 RCD, DDR6 第一子代), so translation_basis is "
      "not_translated; shipment-share and roadmap statements are the company's own characterization, "
      "not independently verified -- recorded by carrying the row as company-sourced (claim_type="
      "company_claim, source_speaker=company) rather than by downgrading evidence_category, which is kept "
      "at its original verified_fact value.",
      "verified_fact", "current", "none", "use_normally",
      "supports_durable_conclusion", ("zh-CN", "not_translated")),

    r("montage_688008_p27", "segments", "reported_metric",
      "2026H1 the four new interconnect products (MRCD/MDB, PCIe Retimer, CKD, CXL MXC) had combined "
      "revenue of 5.38亿元, +80.7% yoy, against 互连类芯片 total of 31.11亿元 (+26.4%).",
      "company", "verified", "L1", "2026-08-29", "2026-08-29",
      "pdftext/2026-08-29_...半年度报告.txt L1262-1264", "high", NA,
      "source=2026年半年度报告 §三(一); the 互连类 total here (+26.4%) is a different comparator basis "
      "from P11's segment-note total, noted to avoid a spurious mismatch; unit basis RMB.",
      "verified_fact", "current", "none", "use_normally",
      "supports_durable_conclusion"),

    r("montage_688008_p28", "competitive_position", "guidance",
      "No numeric 2026 company guidance exists; the 2026 operating plan in the annual report is "
      "qualitative only -- complete engineering development of DDR5 sixth-generation RCD and "
      "third-generation MRCD/MDB, start engineering development of the first-generation DDR6 memory "
      "interconnect product, and tape out engineering samples of PCIe 7.0 Retimer, PCIe Switch and "
      "Ethernet PHY Retimer.",
      "company", "verified", "L1", "2026-10-02", "2026-10-02",
      "pdftext/2026-03-31_...2025年年度报告.txt; raw/cninfo_announcements.json", "high", NA,
      "source=2025年年度报告 经营计划, verified absent from all 2026 filings; no numeric guidance "
      "row should be created; source_date=2026-10-02 is the case research date because the row is an "
      "absence-of-guidance statement checked across the whole 2026 filing set, while the underlying plan "
      "text is from the 2026-03-31 annual report; evidence_category raised from the original "
      "company_statement to management_guidance because claim_type is judgment-bearing guidance; "
      "original_excerpt=（1）内存互连领域……完成DDR5 第六子代RCD、第三子代MRCD/MDB 芯片的工程研发；"
      "积极参与JEDEC 组织对DDR6 内存接口芯片标准的制定，并启动DDR6 第一子代内存互连产品的工程研发。"
      "（2）PCIe/CXL 互连领域……完成PCIe7.0Retimer、PCIeSwitch 芯片工程样片的流片。"
      "（3）以太网互连领域……并计划完成工程样片的流片。; "
      "translated_summary=qualitative 2026 operating plan with no numeric revenue or margin target",
      "management_guidance", "current", "none", "use_normally",
      "supports_working_view"),

    r("montage_688008_f01", "consensus", "forecast",
      "Third-party consensus (THS F10, 17 institutions, as of 2026-09-30): 2026E 归母净利 35.22亿元 "
      "(range 29.30-40.88), EPS 2.89, revenue 74.26亿元; 2027E 46.77亿元, EPS 3.83; 2028E 62.38亿元, "
      "EPS 5.11.",
      "sellside", "unverified", "L5", "2026-09-30", "2026-09-30",
      "reported by fundamentals research stream", "medium", NA,
      "source=同花顺 F10 consensus aggregating 17 institutions; no local raw file was retained -- the "
      "figures arrive via the research stream, so the row stays unverified; used as the consensus input "
      "for M03.",
      "estimate", "current", "not_checked", "attribute",
      "supports_working_view"),

    r("montage_688008_f02", "consensus", "forecast",
      "Individual sell-side 2026E 归母净利: 华泰 38.92亿 (2026-09-03), 财信 37.83亿 (09-01), 国海 40.88亿 "
      "(08-31), 中国银河 39.72亿 (08-31), 国信 36.17亿 (08-30), 开源 38.76亿 (08-30), 中航 31.81亿 "
      "(07-25), 浙商 34.61亿 (06-05), 天风 31.96亿 (05-29).",
      "sellside", "unverified", "L5", "2026-09-30", "2026-09-30",
      "reported by fundamentals research stream", "medium", NA,
      "source=sell-side notes as reported by the research stream; individual note dates span 2026-05-29 "
      "to 2026-09-03 while source_date is the 2026-09-30 consensus observation date; no note files "
      "retained locally, so estimates are unverified and attributed.",
      "estimate", "current", "not_checked", "attribute",
      "supports_working_view"),

    r("montage_688008_f03", "consensus", "forecast",
      "Target prices conflict: Morgan Stanley RMB 377 versus Bernstein RMB 400 (2026-09-21), a 6% spread "
      "with no reconciliation available.",
      "sellside", "unverified", "L5", "2026-09-21", "2026-09-21",
      "reported by fundamentals research stream", "low", NA,
      "source=sell-side notes as reported by the research stream; unit basis RMB per share; no "
      "reconciliation of the two targets was obtainable, treatment=exclude so the conflict does not "
      "enter the valuation conclusion chain.",
      "contradicted", "current", "unresolved", "exclude",
      "blocks_actionability"),

    r("montage_688008_f04", "industry", "reported_metric",
      "Market share: the only prospectus-grade figure is 36.8% (2024, #1 globally, Frost & Sullivan as "
      "cited in the H-share prospectus); media and retail claims of 41% / 43-45% / 46% are unsourced; "
      "CR3 (Montage + Renesas + Rambus) is above 90% and no Chinese mass-production competitor exists.",
      "sellside", "unverified", "L5", "2026-10-02", "2026-10-02",
      "reported by fundamentals research stream", "medium", NA,
      "source=Frost & Sullivan figure via the H-share prospectus, relayed by the research stream; the "
      "36.8% share is not independently verified in this case and the alternative 41-46% claims are "
      "unsourced; source_date=2026-10-02 (case research date) because the relay has no dated publication; "
      "confidence capped at medium for that reason; downgrade_control=conflict_status=unresolved, so the "
      "row is carried as conflicted evidence (evidence_category=contradicted) and cannot ground a "
      "standalone share conclusion -- only the attributed Frost & Sullivan 36.8% leg is usable.",
      "contradicted", "current", "unresolved", "attribute",
      "supports_working_view"),

    r("montage_688008_m01", "financials", "derived_calculation",
      "TTM (2025Q3-2026Q2) revenue 61.58亿元; TTM reported 归母 30.74亿元; TTM 扣非归母 22.53亿元.",
      "mira", "modeled", "L6", "2026-09-30", "2026-09-30",
      "final_metrics.py §B", "high",
      "montage_688008_p05;montage_688008_p06;montage_688008_p15",
      "source=Mira calculation from the quarterly series plus FY2025 primary figures; "
      "Formula: TTM = FY2025 (P15) - 2025H1 + 2026H1 (P05, P06): revenue 54.56 - 26.33 + 33.35 = 61.58亿元; "
      "扣非 = 20.22 - 10.91 + 13.22 = 22.53亿元; limitation=the TTM reported 归母 leg (30.74亿元) cannot be "
      "traced to any row in this log (the 2025H1 归母 comparative and the quarterly itemized P&L are not "
      "logged), and the standalone vendor quarterly financial files have no source_id here -- recorded as "
      "a provenance gap rather than back-filled.",
      "estimate", "current", "none", "use_normally",
      "supports_durable_conclusion", MIRA_LANG),

    r("montage_688008_m02", "valuation", "derived_calculation",
      "Valuation at 202.31 CNY and 12.222亿 shares: P/E TTM reported 80.4x, P/E TTM 扣非 109.8x, P/E on "
      "annualised 2026H1 reported 61.9x, P/E on annualised 2026H1 扣非 93.5x, P/S 40.2x, P/B 11.5x, "
      "EV 2,338.9亿元 and EV/TTM 扣非 103.8x.",
      "mira", "modeled", "L6", "2026-09-30", "2026-09-30",
      "final_metrics.py §B", "high",
      "montage_688008_p01;montage_688008_p03;montage_688008_m01",
      "source=Mira valuation calculation, market cap from P04's inputs; "
      "Formula: market cap = 202.31 x 12.222亿 shares = 2,472.6亿元; EV = market cap - net cash "
      "133.7亿元 = 2,338.9亿元; P/E TTM 扣非 = 2,472.6 / 22.53 = 109.8x; "
      "limitation=the net cash 133.7亿元 comes from the memo's balance-sheet line and has no evidence row "
      "of its own in this log; the annualisation basis (2026H1 x 2) is a shortcut, not a forecast.",
      "estimate", "current", "none", "use_normally",
      "supports_durable_conclusion", MIRA_LANG),

    r("montage_688008_m03", "valuation", "derived_calculation",
      "On consensus 2026E of 35.22亿元 the P/E is 70.2x; on 2027E of 46.77亿元 it is 52.9x; on 2028E of "
      "62.38亿元 it is 39.6x; EV/consensus 2027E is 50.0x.",
      "mira", "modeled", "L6", "2026-09-30", "2026-09-30",
      "final_metrics.py §B", "medium",
      "montage_688008_f01;montage_688008_m01",
      "source=Mira calculation on the F01 consensus; "
      "Formula: P/E 2026E = 2,472.6 / 35.22 = 70.2x; 2027E = 2,472.6 / 46.77 = 52.9x; "
      "2028E = 2,472.6 / 62.38 = 39.6x; EV/2027E = 2,338.9 / 46.77 = 50.0x; "
      "limitation=inherits the unverified status of the F01 consensus and the net-cash line used for EV; "
      "medium confidence for that reason.",
      "estimate", "current", "none", "sensitize",
      "supports_working_view", MIRA_LANG),

    r("montage_688008_m04", "valuation", "derived_calculation",
      "Reverse DCF (10% discount rate, 3% terminal growth, EV 2,338.9亿元): on FY2025 FCF of 17.56亿元 it "
      "requires ~33-34% flat 10-year FCF growth; on annualised 2026H1 FCF of 21.46亿元 it requires ~30%.",
      "mira", "modeled", "L6", "2026-09-30", "2026-09-30",
      "final_metrics.py §E", "medium",
      "montage_688008_m02;montage_688008_m05",
      "source=Mira reverse DCF; Formula: solve g in EV = sum(FCF_0 x (1+g)^t / (1.10)^t, t=1..10) + "
      "terminal value at g_terminal=3%, with EV=2,338.9亿元 and FCF_0 = 17.56亿元 (FY2025) or 21.46亿元 "
      "(2026H1 annualised) -> g ~ 33-34% and ~30% respectively; "
      "limitation=the FCF inputs are themselves derived from the vendor cash-flow series with no evidence "
      "row here, and the 10% discount rate is an explicit judgment (8.5% would lower the required growth "
      "to ~25-26%); the sensitivity is stated rather than modelled.",
      "estimate", "current", "none", "sensitize",
      "supports_durable_conclusion", MIRA_LANG),

    r("montage_688008_m05", "valuation", "derived_calculation",
      "Historical delivery: revenue CAGR 2016-2025 = 23.0%, FCF CAGR 2016-2025 = 18.5%, revenue CAGR "
      "2019-2025 = 21.0%; the consensus implies a 2025-2028 归母 CAGR of 40.8%.",
      "mira", "modeled", "L6", "2026-09-30", "2026-09-30",
      "final_metrics.py §E", "high",
      "montage_688008_p15;montage_688008_f01",
      "source=Mira CAGR calculation; Formula: CAGR = (end/start)^(1/years) - 1 over the vendor annual "
      "revenue and FCF series (23.0% 2016-2025, 18.5% FCF, 21.0% 2019-2025); consensus leg = "
      "(62.38 / 22.36)^(1/3) - 1 = 40.8% using FY2025 归母 from P15 and 2028E from F01; "
      "limitation=the historical annual series (FY2016-FY2024) has no evidence row in this log and the "
      "underlying vendor files raw/income_annual.json, raw/cf_annual.json exist without a source_id.",
      "estimate", "current", "none", "sensitize",
      "supports_durable_conclusion", MIRA_LANG),

    r("montage_688008_t01", "market_data", "derived_calculation",
      "All-time high (forward-adjusted intraday) 332.51 on 2026-07-01 with a close peak of 315.50; the "
      "2026-09-30 close is -35.9% below that peak; max drawdown in 2026 was -41.9% (2026-07-01 to "
      "2026-07-17), versus -49.9% in 2022 and -64.1% in 2020-2022.",
      "mira", "modeled", "L6", "2026-09-30", "2026-09-30",
      "tech.py", "high", "montage_688008_p01",
      "source=Mira calculation from the hithink-finance forward-adjusted history series; "
      "Formula: peak-to-last = 202.31 / 315.50 - 1 = -35.9% on closes; "
      "max drawdown = min over window of (P_t / running max - 1) = -41.9% (2026-07-01 to 2026-07-17); "
      "cross-check_caveat=this row's intraday all-time high of 332.51 is 0.39 CNY below the 332.90 "
      "intraday peak carried in T07 -- left unreconciled and disclosed rather than silently aligned.",
      "estimate", "current", "none", "use_normally",
      "supports_durable_conclusion", MIRA_LANG),

    r("montage_688008_t02", "market_data", "derived_calculation",
      "Trailing total returns (forward-adjusted, dividend-inclusive): 3M -35.9%, 6M +56.1%, 1Y +31.2%, "
      "2Y +207.0%, 3Y +317.8%; YTD 2026 +72.3%.",
      "mira", "modeled", "L6", "2026-09-30", "2026-09-30",
      "tech.py", "high", "montage_688008_p01",
      "source=Mira calculation from the forward-adjusted, dividend-inclusive history series; "
      "Formula: total return = P_end x adj_factor_end / (P_start x adj_factor_start) - 1 for each "
      "trailing window ending 2026-09-30; the adjusted series (raw/history_forward.json) has no source_id "
      "of its own in this log.",
      "estimate", "current", "none", "use_normally",
      "supports_durable_conclusion", MIRA_LANG),

    r("montage_688008_t03", "market_data", "derived_calculation",
      "Realised annualised volatility 74.9% (250d), 66.2% (500d) and 57.3% (full 7 years); beta to 科创50 "
      "1.36 with correlation 0.79.",
      "mira", "modeled", "L6", "2026-09-30", "2026-09-30",
      "relative.py", "medium", "montage_688008_p01",
      "source=Mira calculation from the hithink-finance daily history and the 科创50 index history; "
      "Formula: annualised vol = stdev(daily log returns) x sqrt(252) over each window; "
      "beta = cov(stock, 科创50) / var(科创50); correlation = 0.79; "
      "limitation=beta is estimated on daily data only and the index series has no evidence row here; "
      "medium confidence.",
      "estimate", "current", "none", "use_normally",
      "supports_durable_conclusion", MIRA_LANG),

    r("montage_688008_t04", "market_data", "derived_calculation",
      "Price versus moving averages: MA20 +0.9%, MA60 -4.1%, MA120 -8.0%, MA250 +14.0%; the close sits in "
      "the 89.1st percentile of the trailing 3-year close distribution.",
      "mira", "modeled", "L6", "2026-09-30", "2026-09-30",
      "tech.py", "high", "montage_688008_p01",
      "source=Mira calculation from the forward-adjusted history series; "
      "Formula: deviation = 202.31 / MA_n - 1 for n in {20, 60, 120, 250}; percentile = share of the "
      "trailing 3-year closes at or below 202.31 = 89.1st; measured on adjusted closes.",
      "estimate", "current", "none", "use_normally",
      "supports_working_view", MIRA_LANG),

    r("montage_688008_t05", "market_reaction", "derived_calculation",
      "3M relative performance: 688008 -35.9% versus 芯片概念 -19.9%, 存储芯片 -30.1%, 科创50 -28.9% and "
      "沪深300 -12.1%; 12M relative: 688008 +30.7% versus the 存储芯片 index +48.5%, so it underperformed "
      "the memory complex over 12 months.",
      "mira", "modeled", "L6", "2026-09-30", "2026-09-30",
      "relative.py", "high", "montage_688008_p01",
      "source=Mira calculation from the index and stock histories; "
      "Formula: relative return = stock total return - index total return over each window; "
      "the four index series (芯片概念, 存储芯片, 科创50, 沪深300) have no evidence rows in this log; "
      "index names are the vendor's concept indices, not official sector classifications.",
      "estimate", "current", "none", "use_normally",
      "supports_durable_conclusion", MIRA_LANG),

    r("montage_688008_t06", "market_data", "derived_calculation",
      "20-day average turnover 63.5亿元/day versus a 250-day average of 94.0亿元/day, so liquidity has "
      "contracted about 32% from the 12-month norm.",
      "mira", "modeled", "L6", "2026-09-30", "2026-09-30",
      "tech.py", "high", "montage_688008_p01",
      "source=Mira calculation from the vendor daily turnover series; "
      "Formula: contraction = 63.5 / 94.0 - 1 = -32.4%; turnover is a value measure (亿元/day), so it "
      "mixes price and volume effects and is not a pure volume read.",
      "estimate", "current", "none", "use_normally",
      "supports_working_view", MIRA_LANG),

    r("montage_688008_t07", "market_reaction", "interpretation",
      "The price peak (2026-07-01, 332.90 intraday) preceded the Korean raid disclosure of 2026-07-17 by "
      "12 trading days, and the two worst days (-17.0% on 07-16 and -13.1% on 07-17) straddle the raid "
      "date and its disclosure.",
      "mira", "modeled", "L6", "2026-09-30", "2026-09-30",
      "trace.py; pdftext/2026-07-17_...", "medium",
      "montage_688008_p01;montage_688008_t01;montage_688008_p17",
      "source=Mira event-window measurement, raid date from P17; "
      "Formula: gap = trading days between 2026-07-01 peak and 2026-07-17 disclosure = 12; "
      "event-window returns = daily closes 2026-07-16 and 2026-07-17; "
      "caveat=the 332.90 intraday peak here differs from T01's 332.51 -- the two rows are not "
      "reconciled; sequencing is not causation, the market may have been repricing other factors.",
      "inference", "current", "none", "sensitize",
      "supports_working_view", MIRA_LANG),

    r("montage_688008_g01", "accounting_quality", "derived_calculation",
      "2026H1 应收账款 9.75亿元 versus 3.91亿元 a year earlier (+149%) while revenue grew 26.7%, i.e. "
      "receivables growing far ahead of revenue.",
      "mira", "modeled", "L6", "2026-06-30", "2026-06-30",
      "metrics.py §C", "high", "montage_688008_p05",
      "source=Mira calculation from the vendor quarterly balance sheet plus the P05 revenue comparative; "
      "Formula: AR growth = 9.75 / 3.91 - 1 = +149%; growth gap = 149% - 26.7% (P05 revenue) = +122pp; "
      "limitation=the balance-sheet figures come from the vendor quarterly file (raw/bs_quarterly.json) "
      "which has no source_id in this log, and the H1 report's own receivables line was not re-read.",
      "estimate", "current", "none", "sensitize",
      "blocks_actionability", MIRA_LANG),

    r("montage_688008_g02", "accounting_quality", "reported_metric",
      "Inventory turnover 1.2227x for 2026H1 versus 3.3019x for FY2025 and 3.6512x for FY2024, a sharp "
      "slowdown on a half-year basis.",
      "vendor_aggregator", "verified", "L5", "2026-08-29", "2026-08-29",
      "raw/ind_2026-2.json", "high", NA,
      "source=hithink-finance financials.indicators report 2026-2; unit basis=times; "
      "basis_caveat=the 1.2227x is a half-year figure while 3.3019x/3.6512x are full-year figures as "
      "reported by the vendor, so the comparison is not like-for-like (annualising the H1 figure would "
      "roughly double it) -- stated rather than adjusted, because the vendor basis is not documented here.",
      "reported_fact", "current", "none", "sensitize",
      "blocks_actionability"),

    r("montage_688008_g03", "accounting_quality", "reported_metric",
      "2026H1 净利现金含量 66.49% and 经营现金流/收入 39.82%, down from 119.8% and 46.5% in FY2024; "
      "经营现金流 13.28亿元 against 归母净利 19.97亿元.",
      "vendor_aggregator", "verified", "L5", "2026-08-29", "2026-08-29",
      "raw/ind_2026-2.json; final_metrics.py", "high", NA,
      "source=hithink-finance financials.indicators 2026-2 for the ratios combined with the "
      "2026年半年度报告 cash-flow and profit lines; the ratio block is vendor-computed (L5) while the "
      "13.28/19.97亿元 pair is filing-grade; the two provenances are split inside one row -- ratio legs "
      "attributed to the vendor.",
      "reported_fact", "current", "none", "sensitize",
      "blocks_actionability"),

    r("montage_688008_g04", "accounting_quality", "reported_metric",
      "研发投入占营业收入比例 13.58% in 2026H1 versus 13.56% in 2025H1 (flat yoy) but down from 29.8% in "
      "FY2023 and 21.0% in FY2024; FY2025 R&D spend grew ~20% against revenue +49.9%.",
      "company", "verified", "L1", "2026-08-29", "2026-08-29",
      "pdftext/2026-08-29_...半年度报告.txt L476", "high", NA,
      "source=2026年半年度报告 主要财务指标 plus the FY2025 业绩快报 (P15 for the +49.9% revenue leg); "
      "the FY2023/FY2024 R&D ratios come from the same filing's multi-year comparison; the ratio itself "
      "is a primary filing disclosure rather than a Mira derivation.",
      "verified_fact", "current", "none", "sensitize",
      "supports_working_view"),

    r("montage_688008_g05", "accounting_quality", "derived_calculation",
      "Robustness note on the 2026Q2 gross-margin read: the pre-announcement's implied Q2 interconnect "
      "gross margin (~67.4% as given) is consistent with the final H1 report, but the trading-service "
      "derived Q2 total gross margin of 61.83% was not cross-verified against a company figure for the "
      "quarter, being derived as cumulative H1 minus cumulative Q1.",
      "mira", "modeled", "L6", "2026-10-02", "2026-10-02",
      "final_metrics.py", "medium",
      "montage_688008_p05;montage_688008_p11;montage_688008_p12;montage_688008_g08",
      "source=Mira methodology note; Formula: Q2 total GM = (2026H1 cumulative revenue and cost) minus "
      "(2026Q1 cumulative revenue and cost), then GM = 1 - cost/revenue = 61.83%; the 67.4% interconnect "
      "leg is company-given (P14) and does agree; open_limitation=no company-published single-quarter "
      "total gross margin exists to cross-check against, so this number stays sensitivity-grade.",
      "inference", "current", "none", "sensitize",
      "blocks_actionability", MIRA_LANG),

    r("montage_688008_g06", "source_gap", "assumption",
      "The non-operating line composition below 营业利润 is only partially resolved: 投资收益 2.52亿元 and "
      "the fair-value line are known, but a full bridge from 营业利润 20.71亿元 to 利润总额 20.71亿元 at "
      "quarterly granularity was not reconstructed.",
      "mira", "unverified", "L6", "2026-10-02", "2026-10-02",
      "pdftext/2026-08-29_...半年度报告.txt L3717-3724", "medium",
      "montage_688008_p06",
      "source=Mira source gap; the missing bridge is what would split operating growth from FX and other "
      "non-operating items at quarterly granularity; Formula: n/a (no bridge reconstructed); "
      "upstream note=P06 is the closest logged input (non-recurring and investment-income components), "
      "the half-year report's L3717-3724 block is the raw source of the 营业利润/利润总额 pair; "
      "no independent source was obtained, so the row stays unverified and blocks actionability.",
      "unknown", "current", "not_checked", "source_gap",
      "blocks_actionability", MIRA_LANG),

    r("montage_688008_g07", "source_gap", "fact",
      "Web search and web fetch were unavailable at the harness level in this session (search endpoint "
      "HTTP 404 misconfiguration; fetch blocked by the SSRF guard on a fake-IP proxy range), so all "
      "evidence came from the hithink-finance CLI (THS vendor) and CNINFO primary filings.",
      "mira", "verified", "L6", "2026-10-02", "2026-10-02",
      "n/a", "high", "montage_688008_g06",
      "source=Mira environment note; directly observable in this session and corroborated by case-notes.md; "
      "upstream note=the outage is the reason G06's quarterly non-operating bridge could not be closed; "
      "consequence=zero independent third-party industry coverage in this case, so MRDIMM timing and "
      "market-share claims rest on company statements only; blocks_publication until an independent "
      "industry source is obtained.",
      "verified_fact", "current", "none", "open_item",
      "blocks_publication", MIRA_LANG),

    r("montage_688008_g08", "accounting_quality", "derived_calculation",
      "The 2026Q2 quarterly gross margin of 61.83% and quarterly 营业利润 of 11.94亿元 are Mira-derived "
      "by differencing cumulative YTD figures; the company does not publish single-quarter statements in "
      "the pulled data.",
      "mira", "modeled", "L6", "2026-09-30", "2026-09-30",
      "metrics.py §B", "high", "montage_688008_p05",
      "source=Mira calculation from the vendor cumulative quarterly data; "
      "Formula: Q2 value = 2026H1 cumulative (P05 period) - 2026Q1 cumulative, applied to revenue, cost "
      "and 营业利润, giving Q2 total GM 61.83% and Q2 营业利润 11.94亿元; "
      "limitation=the vendor cumulative quarterly financial files have no source_id in this log and the "
      "company publishes no single-quarter statements, so the differencing assumption (no restatement "
      "between quarters) is untested.",
      "estimate", "current", "none", "use_normally",
      "supports_working_view", MIRA_LANG),
]

assert len(ROWS) == 58, len(ROWS)

OUT = CASE / "evidence-log.csv"
with OUT.open("w", newline="", encoding="utf-8") as fh:
    writer = csv.DictWriter(fh, fieldnames=HEADER, lineterminator="\n")
    writer.writeheader()
    for row in ROWS:
        assert set(row) == set(HEADER), set(row) ^ set(HEADER)
        writer.writerow(row)

print(f"wrote {len(ROWS)} data rows -> {OUT}")
for field in ("claim_type", "verification_status", "authority_level", "claim_area",
              "source_language", "translation_basis", "source_speaker",
              "evidence_category", "treatment", "readiness_impact"):
    counts = Counter(row[field] for row in ROWS)
    print(f"{field}: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

derived = [row["source_id"] for row in ROWS
           if row["claim_type"] == "derived_calculation" or row["authority_level"] == "L6"]
print(f"\nderived/L6 rows ({len(derived)}): " + ", ".join(derived))
bad = [row["source_id"] for row in ROWS
       if (row["claim_type"] == "derived_calculation" or row["authority_level"] == "L6")
       and (not row["upstream_sources"] or row["upstream_sources"] == "not_applicable")]
print("derived/L6 rows missing upstream_sources: " + (", ".join(bad) or "none"))
nof = [row["source_id"] for row in ROWS
       if row["claim_type"] == "derived_calculation" and "Formula:" not in row["notes"]]
print("derived_calculation rows missing Formula: " + (", ".join(nof) or "none"))
