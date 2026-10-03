"""Pull 澜起科技 (688008.SH) CNINFO announcement index to resolve the H1 2026 capital event."""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, r"D:\quant\mira\tools")

from mira_data.adapters import cninfo_disclosure as ci  # noqa: E402

OUT = r"D:\quant\mira\analysis\688008-2026-10-02\raw"

code = "688008"
org = ci.resolve_org_id(code)
print(f"orgId for {code} = {org}")

windows = [
    ("2025-09-01", "2026-10-02"),
]
all_rows = []
for since, until in windows:
    se = f"{since}~{until}"
    page = 1
    while page <= 40:
        payload = ci._query_page(stock=f"{code},{org}", se_date=se, page_num=page)
        items = payload.get("announcements") or []
        if page == 1:
            print(f"window {se}: totalAnnouncement={payload.get('totalAnnouncement')} "
                  f"hasMore={payload.get('hasMore')}")
        if not items:
            break
        for it in items:
            all_rows.append({
                "date": ci.bj_date(it.get("announcementTime")),
                "time_bj": ci.bj_time(it.get("announcementTime")),
                "title": ci.clean_title(it.get("announcementTitle")),
                "type": it.get("announcementType"),
                "id": str(it.get("announcementId")),
                "pdf": ci.pdf_url(it.get("adjunctUrl")),
                "event": ci.classify(it)[0],
                "basis": ci.classify(it)[1],
            })
        if not payload.get("hasMore"):
            break
        page += 1
        ci._sleep(0.4)

# dedupe on (date, title)
seen = {}
for r in all_rows:
    seen[(r["date"], r["title"])] = r
rows = sorted(seen.values(), key=lambda r: (r["date"], r["id"]))
print(f"\n=== {len(rows)} unique announcements {rows[0]['date']} .. {rows[-1]['date']} ===\n")

with open(os.path.join(OUT, "cninfo_announcements.json"), "w", encoding="utf-8") as f:
    json.dump(rows, f, ensure_ascii=False, indent=1)

for r in rows:
    print(f"{r['date']}  [{r['event']:<26}] {r['title']}")
