"""Fetch additional key PDFs: full H1 2026 report, 2025 annual report, subsidiary capital increase."""
import io
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, r"D:\quant\mira\tools")
sys.path.insert(0, r"D:\quant\mira\analysis\688008-2026-10-02\_pylibs")
from mira_data.adapters import cninfo_disclosure as ci  # noqa: E402

OUT = r"D:\quant\mira\analysis\688008-2026-10-02\raw"
TXT = r"D:\quant\mira\analysis\688008-2026-10-02\pdftext"
os.makedirs(TXT, exist_ok=True)

rows = json.load(open(os.path.join(OUT, "cninfo_announcements.json"), encoding="utf-8"))

TARGETS = [
    "澜起科技2026年半年度报告",          # full, not 摘要
    "澜起科技2025年年度报告",            # full
    "关于控股子公司增资扩股暨关联交易的公告",
]

import pypdf

for row in rows:
    if row["title"] in ("澜起科技2026年半年度报告摘要",):
        continue
    if not any(row["title"] == t or row["title"] == t for t in TARGETS):
        continue
    url = row["pdf"]
    safe = re.sub(r"[^\w\u4e00-\u9fff]+", "_", row["title"])[:70]
    dest = os.path.join(TXT, f"{row['date']}_{safe}.txt")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": ci.BROWSER_UA})
        with urllib.request.urlopen(req, timeout=120) as resp:
            blob = resp.read()
    except Exception as exc:
        print(f"!! download failed {row['title']}: {exc}")
        continue
    try:
        reader = pypdf.PdfReader(io.BytesIO(blob))
        pages = []
        for pg in reader.pages:
            pages.append(pg.extract_text() or "")
        text = "\n".join(pages)
        if not text.strip():
            text = "[[NO TEXT LAYER - image-only PDF]]"
    except Exception as exc:
        text = f"[[EXTRACT FAILED: {exc}]]"
    with open(dest, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"{row['date']} | {len(blob):>10,}B | pages={len(reader.pages) if 'reader' in dir() else '?'} "
          f"| {len(text):>8,} chars | {row['title'][:50]}")
