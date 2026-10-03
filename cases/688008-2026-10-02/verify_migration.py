#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Independent check of the migrated evidence-log.csv against the legacy 13-col file."""
import csv
from pathlib import Path

CASE = Path(__file__).resolve().parent
NEW = CASE / "evidence-log.csv"
OLD = CASE / "_evidence-log.legacy13.bak.csv"

HEADER = ("source_id,claim_area,claim_type,claim_text,source_speaker,verification_status,"
          "authority_level,source_date,as_of_date,url_or_path,used_by_agent,used_by_skill,"
          "confidence,upstream_sources,notes,evidence_category,freshness_status,conflict_status,"
          "treatment,readiness_impact,source_language,translation_basis")

raw = NEW.read_bytes()
print("BOM present:", raw.startswith(b"\xef\xbb\xbf"))
lines = raw.decode("utf-8").split("\n")
print("header byte-exact:", lines[0] == HEADER)
print("trailing newline:", lines[-1] == "")
print("total lines:", len(lines), "(header + %d data rows)" % (len(lines) - 1 - (1 if lines[-1] == "" else 0)))

with NEW.open(newline="", encoding="utf-8") as fh:
    new_rows = list(csv.DictReader(fh))
with OLD.open(newline="", encoding="utf-8") as fh:
    old_rows = list(csv.DictReader(fh))

print("parsed new rows:", len(new_rows), " legacy rows:", len(old_rows))

# 1. source_id mapping
expect_ids = ["montage_688008_" + r["claim_id"].lower() for r in old_rows]
got_ids = [r["source_id"] for r in new_rows]
print("source_id mapping exact & ordered:", expect_ids == got_ids)

# 2. row-by-row preservation of the carried-over fields
carry = {"claim_text": "claim_text", "as_of_date": "as_of_date", "confidence": "confidence",
         "freshness_status": "freshness_status", "conflict_status": "conflict_status",
         "treatment": "treatment", "readiness_impact": "readiness_impact",
         "evidence_category": "evidence_category", "url_or_path": "source_ref"}
diffs = []
for o, n in zip(old_rows, new_rows):
    for new_f, old_f in carry.items():
        if o[old_f].strip() != n[new_f].strip():
            diffs.append((o["claim_id"], new_f, o[old_f], n[new_f]))
print("carried-field mismatches:", len(diffs))
for d in diffs:
    print("   DIFF", d)

# 3. claim_type mapping coverage -- no legacy label survives
legacy_labels = sorted({o["claim_type"] for o in old_rows})
canonical = {"fact", "reported_metric", "company_claim", "guidance", "target", "commitment",
             "forecast", "assumption", "interpretation", "opinion", "market_pricing", "sentiment",
             "rumor_signal", "derived_calculation"}
print("legacy claim_type labels:", legacy_labels)
print("new claim_types all canonical:", {r["claim_type"] for r in new_rows} <= canonical)

# 4. verification_status remap check
remap = {"calculated": "modeled", "reported": "disclosed", "verified": "verified", "unverified": "unverified"}
bad = [(o["claim_id"], o["verification_status"], n["verification_status"])
       for o, n in zip(old_rows, new_rows) if remap[o["verification_status"]] != n["verification_status"]]
print("verification_status remap mismatches:", bad)

# 5. derived/L6 invariants + judgment-bearing excerpt requirement
prob = []
for r in new_rows:
    d = r["claim_type"] == "derived_calculation" or r["authority_level"] == "L6"
    if d and (not r["upstream_sources"].strip() or r["upstream_sources"].strip() == "not_applicable"):
        prob.append((r["source_id"], "missing upstream_sources"))
    if r["claim_type"] == "derived_calculation" and "Formula:" not in r["notes"]:
        prob.append((r["source_id"], "missing Formula"))
    if (r["claim_type"] in {"guidance", "company_claim", "commitment", "target"}
            and r["translation_basis"] in {"mira_translation", "provider_translation"}
            and "original_excerpt" not in r["notes"]):
        prob.append((r["source_id"], "missing original_excerpt"))
    if not r["source_language"].strip():
        prob.append((r["source_id"], "empty source_language"))
print("invariant problems:", prob or "none")

# 6. upstream ids all resolve to real rows
ids = set(got_ids)
badref = [(r["source_id"], u) for r in new_rows
          if r["upstream_sources"] != "not_applicable"
          for u in r["upstream_sources"].split(";") if u.strip() not in ids]
print("unresolved upstream refs:", badref or "none")

# 7. old source prose preserved somewhere in notes
missing_prose = []
for o, n in zip(old_rows, new_rows):
    prose = o["source_speaker"]
    if prose in ("Mira calculation", "Mira judgement", "Mira inference", "Mira environment note",
                 "Mira source gap", "Mira methodology note", "Mira calculation from P05+P06",
                 "Mira calculation; validated against hithink-finance valuation.snapshot",
                 "Mira comparison of company filing (P17) against wire reporting (P17b/P17c)",
                 "Mira calculation; 津逮 Q1 figure from company segment disclosure",
                 "Mira calculation from quarterly + FY2025 primary figures",
                 "Mira calculation on F01 consensus", "Mira calculation from hithink-finance history",
                 "Mira calculation from index histories", "Mira calculation; raid date from P17",
                 "Mira calculation from hithink-finance quarterly balance sheet",
                 "Mira cross-check of primary filing vs subagent research summary",
                 "Mira inference from P17b/P17c plus margin history (E/P-statement series)"):
        continue
    if prose.split(";")[0].strip() not in n["notes"]:
        missing_prose.append((o["claim_id"], prose[:70]))
print("legacy source prose not carried into notes:", missing_prose or "none")
