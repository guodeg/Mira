"""Fetch and text-extract key CNINFO PDFs for 688008.SH."""
import io
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, r"D:\quant\mira\tools")
from mira_data.adapters import cninfo_disclosure as ci  # noqa: E402
import mira_data.net as net  # noqa: E402

OUT = r"D:\quant\mira\analysis\688008-2026-10-02\raw"
TXT = r"D:\quant\mira\analysis\688008-2026-10-02\pdftext"
os.makedirs(TXT, exist_ok=True)

rows = json.load(open(os.path.join(OUT, "cninfo_announcements.json"), encoding="utf-8"))

TARGETS = [
    "2026年半年度业绩预增的自愿性公告",
    "关于配合韩国相关调查的说明公告",
    "关于H股挂牌并上市交易的公告",
    "关于H股公开发行价格的公告",
    "关于控股子公司增资扩股暨关联交易的公告",
    "2026年半年度报告摘要",
    "2026年中期利润分配方案公告",
    "2025年年度业绩预增公告",
    "关于出售资产的公告",
    "2025年度业绩快报公告",
]

try:
    import pypdf  # noqa: F401
    ENGINE = "pypdf"
except Exception:
    try:
        import fitz  # noqa: F401
        ENGINE = "pymupdf"
    except Exception:
        ENGINE = None
print(f"PDF engine: {ENGINE}")
print()

for row in rows:
    if not any(t in row["title"] for t in TARGETS):
        continue
    url = row["pdf"]
    safe = re.sub(r"[^\w\u4e00-\u9fff]+", "_", row["title"])[:70]
    dest = os.path.join(TXT, f"{row['date']}_{safe}.txt")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": ci.BROWSER_UA})
        with urllib.request.urlopen(req, timeout=60) as resp:
            blob = resp.read()
    except Exception as exc:
        print(f"!! {row['date']} {row['title'][:40]} download failed: {exc}")
        continue
    text = ""
    if ENGINE == "pypdf":
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(blob))
        text = "\n".join((pg.extract_text() or "") for pg in reader.pages)
    elif ENGINE == "pymupdf":
        import fitz
        doc = fitz.open(stream=blob, filetype="pdf")
        text = "\n".join(pg.get_text() for pg in doc)
    with open(dest, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"{row['date']} | {len(blob):>9,}B | {len(text):>7,} chars | {row['title'][:52]}")
