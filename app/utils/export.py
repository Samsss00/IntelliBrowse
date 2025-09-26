# app/utils/export.py
import csv, json, os
from typing import List, Dict

def save_results(run_dir: str, results: List[Dict]):
    os.makedirs(run_dir, exist_ok=True)
    csv_path = os.path.join(run_dir, "results.csv")
    json_path = os.path.join(run_dir, "results.json")

    # CSV
    if results:
        keys = ["title", "price", "price_value", "link"]
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for r in results:
                w.writerow({k: r.get(k) for k in keys})
    else:
        # still create empty CSV with headers
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["title","price","price_value","link"])

    # JSON
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    return {"csv": csv_path, "json": json_path}
