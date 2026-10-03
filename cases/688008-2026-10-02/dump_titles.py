import json
import sys

sys.stdout.reconfigure(encoding="utf-8")
rows = json.load(open(r"D:\quant\mira\analysis\688008-2026-10-02\raw\cninfo_announcements.json",
                     encoding="utf-8"))
OUT = r"D:\quant\mira\analysis\688008-2026-10-02\cninfo_titles.txt"
with open(OUT, "w", encoding="utf-8") as f:
    f.write(f"# 澜起科技 688008.SH CNINFO announcement index: {len(rows)} rows "
            f"{rows[0]['date']} .. {rows[-1]['date']}\n\n")
    for r in rows:
        f.write(f"{r['date']}  [{r['event']:<24}] {r['title']}\n")
print(f"wrote {OUT} with {len(rows)} rows")
