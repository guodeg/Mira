"""Normalize hithink-finance raw JSON envelopes for 688008.SH into readable tables."""
import json
import os

RAW = r"D:\quant\mira\analysis\688008-2026-10-02\raw"


def load(name):
    with open(os.path.join(RAW, name), "r", encoding="utf-8") as f:
        return json.load(f)


def show(name, max_items=3):
    env = load(name)
    data = env.get("data")
    print("=" * 100)
    print(f"FILE: {name}")
    print(f"META: {json.dumps(env.get('meta', {}), ensure_ascii=False)}")
    if isinstance(data, dict):
        print(f"DATA KEYS: {list(data.keys())}")
        for k, v in data.items():
            if k in ("path", "format"):
                continue
            if isinstance(v, list):
                print(f"  {k}: list len={len(v)}")
                for item in v[:max_items]:
                    print(f"    {json.dumps(item, ensure_ascii=False)}")
            else:
                print(f"  {k}: {json.dumps(v, ensure_ascii=False)}")
    elif isinstance(data, list):
        print(f"DATA: list len={len(data)}")
        for item in data[:max_items]:
            print(f"  {json.dumps(item, ensure_ascii=False)}")
    else:
        print(f"DATA: {json.dumps(data, ensure_ascii=False)}")
    print()


for f in ["snapshot.json", "valuation.json", "ind_2025-4.json", "income_annual.json"]:
    show(f)

print("#" * 100)
print("ALL INDICATOR FILES - FULL DUMP")
for r in ["2020-4", "2021-4", "2022-4", "2023-4", "2024-4", "2025-4", "2026-1", "2026-2"]:
    env = load(f"ind_{r}.json")
    print(f"--- {r} ---")
    print(json.dumps(env.get("data"), ensure_ascii=False, indent=1))
