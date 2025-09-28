# app/utils/export.py
"""
Export utilities for Web Navigator AI Agent.

Outputs a clean CSV + JSON with:
- Stable column order (Excel/Sheets-friendly, UTF-8 BOM).
- why_choose  : short human summary.
- score       : 0–1 ranking (RAM/CPU/SSD/price/GPU/screen).
- pros/cons   : concise bullet-like text (JSON also includes arrays).
- image       : optional thumbnail URL if available.

Public API:
    save_results(run_dir: str, results: list[dict]) -> dict[str, str]
"""

from __future__ import annotations
import csv
import json
import math
import os
from typing import Any, Dict, List, Optional


# ---------- Parse helpers ----------

def _to_int(x: Any) -> Optional[int]:
    try:
        if x is None or x == "":
            return None
        if isinstance(x, str):
            digits = "".join(ch for ch in x if ch.isdigit())
            return int(digits) if digits else None
        return int(x)
    except Exception:
        return None


def _to_float(x: Any) -> Optional[float]:
    try:
        if x is None or x == "":
            return None
        return float(x)
    except Exception:
        return None


def _norm_str(x: Any) -> str:
    if x is None:
        return ""
    return str(x).strip()


# ---------- Heuristics: Why / Score / Pros-Cons ----------

def _cpu_tier(cpu: str) -> int:
    s = (cpu or "").lower()
    if not s:
        return 0
    if "i9" in s or "ryzen 9" in s or "m3 max" in s or "m3 pro" in s:
        return 5
    if "i7" in s or "ryzen 7" in s or "m3" in s or "m2 pro" in s:
        return 4
    if "i5" in s or "ryzen 5" in s or "m2" in s or "m1 pro" in s:
        return 3
    if "i3" in s or "ryzen 3" in s or "m1" in s:
        return 2
    return 1


def _has_discrete_gpu(gpu: str) -> bool:
    s = (gpu or "").lower()
    return "rtx" in s or "gtx" in s or "radeon" in s


def _build_why_choose(row: Dict[str, Any]) -> str:
    bits: List[str] = []

    # Price bucket
    pv = _to_int(row.get("price_value"))
    if isinstance(pv, int):
        if pv < 35000:   bits.append("great value")
        elif pv < 50000: bits.append("good price")
        else:            bits.append("premium tier")

    # RAM
    ram = _to_int(row.get("ram_gb"))
    if isinstance(ram, int):
        if ram >= 16: bits.append(f"{ram}GB RAM")
        elif ram >= 8: bits.append("8GB RAM")

    # CPU
    cpu = _norm_str(row.get("cpu"))
    if cpu:
        families = ["i3", "i5", "i7", "i9", "Ryzen 3", "Ryzen 5", "Ryzen 7", "Ryzen 9", "M1", "M2", "M3"]
        for tok in families:
            if tok.lower() in cpu.lower():
                bits.append(tok)
                break
        else:
            bits.append(cpu.split()[0])

    # SSD
    ssd = _to_int(row.get("storage_ssd_gb"))
    if isinstance(ssd, int):
        if ssd >= 512: bits.append("512GB+ SSD")
        elif ssd >= 256: bits.append("256GB SSD")

    # GPU
    if _has_discrete_gpu(row.get("gpu") or ""):
        bits.append("discrete GPU")

    # OS & screen
    os_val = _norm_str(row.get("os"))
    if os_val: bits.append(os_val.upper())

    scr = _to_float(row.get("screen_inches"))
    if isinstance(scr, float):
        bits.append('15"+ screen' if scr >= 15 else '~14" screen')

    # Brand last
    brand = _norm_str(row.get("brand"))
    if brand: bits.append(brand)

    # Dedup + shorten
    seen, out = set(), []
    for b in bits:
        k = b.lower()
        if k not in seen:
            seen.add(k)
            out.append(b)
    return " • ".join(out[:6])


def _score_item(row: Dict[str, Any]) -> float:
    """
    Score 0..1 using UI-aligned weights.
    No budget context at export time; normalize price to [20k..150k].
    """
    w = {"price": 0.35, "ram": 0.20, "cpu": 0.20, "ssd": 0.12, "gpu": 0.08, "screen": 0.03, "os": 0.02}

    # Price (lower is better)
    price = _to_float(row.get("price_value"))
    if price is None or not math.isfinite(price):
        price = float("inf")
    price_score = max(0.0, min(1.0, (150000 - price) / 130000))

    # RAM
    ram = _to_int(row.get("ram_gb"))
    if isinstance(ram, int):
        if ram >= 32: ram_score = 1.0
        elif ram >= 16: ram_score = 0.8
        elif ram >= 8:  ram_score = 0.5
        else:           ram_score = 0.2
    else:
        ram_score = 0.0

    # CPU
    cpu_score = min(1.0, _cpu_tier(_norm_str(row.get("cpu"))) / 5.0)

    # SSD
    ssd = _to_int(row.get("storage_ssd_gb"))
    if isinstance(ssd, int):
        if ssd >= 1024: ssd_score = 1.0
        elif ssd >= 512: ssd_score = 0.8
        elif ssd >= 256: ssd_score = 0.5
        else:            ssd_score = 0.2
    else:
        ssd_score = 0.0

    # GPU
    gpu_score = 1.0 if _has_discrete_gpu(_norm_str(row.get("gpu"))) else 0.3

    # Screen
    scr = _to_float(row.get("screen_inches"))
    if isinstance(scr, float):
        if 15 <= scr <= 16: screen_score = 1.0
        elif 13 <= scr < 15: screen_score = 0.8
        else:                screen_score = 0.5
    else:
        screen_score = 0.6  # neutral

    # OS
    os_ = _norm_str(row.get("os")).lower()
    if "windows 11" in os_ or "mac" in os_:
        os_score = 1.0
    elif "windows" in os_:
        os_score = 0.8
    else:
        os_score = 0.5

    score = (
        w["price"]*price_score + w["ram"]*ram_score + w["cpu"]*cpu_score +
        w["ssd"]*ssd_score + w["gpu"]*gpu_score + w["screen"]*screen_score +
        w["os"]*os_score
    )
    return round(max(0.0, min(1.0, score)), 3)


def _build_pros_cons(row: Dict[str, Any]) -> tuple[list[str], list[str]]:
    """
    Very small, explainable rules to keep output clear for all users.
    """
    pros: List[str] = []
    cons: List[str] = []

    price = _to_int(row.get("price_value"))
    if isinstance(price, int):
        if price < 35000: pros.append("Low price")
        elif price > 80000: cons.append("Expensive")

    ram = _to_int(row.get("ram_gb"))
    if isinstance(ram, int):
        if ram >= 16: pros.append("16GB+ RAM")
        elif ram < 8: cons.append("Under 8GB RAM")

    ssd = _to_int(row.get("storage_ssd_gb"))
    if isinstance(ssd, int):
        if ssd >= 512: pros.append("512GB+ SSD")
        elif ssd < 256: cons.append("Small SSD (<256GB)")
    else:
        cons.append("SSD unknown")

    cpu = _norm_str(row.get("cpu"))
    tier = _cpu_tier(cpu)
    if tier >= 4: pros.append("High-tier CPU")
    elif tier <= 1 and cpu: cons.append("Entry-level CPU")

    gpu = _norm_str(row.get("gpu"))
    if _has_discrete_gpu(gpu):
        pros.append("Discrete GPU")
    elif gpu:
        cons.append("Integrated graphics")

    os_val = _norm_str(row.get("os")).lower()
    if "windows 11" in os_val or "mac" in os_val:
        pros.append("Modern OS")
    elif os_val:
        cons.append("Older OS")

    scr = _to_float(row.get("screen_inches"))
    if isinstance(scr, float):
        if 15 <= scr <= 16: pros.append("Comfortable 15–16\" screen")
        elif scr < 14: pros.append("Portable ~14\"")  # also a pro for portability
        elif scr > 16: cons.append("Large & less portable")

    # Dedup + cap lengths
    def _dedup_cap(items: List[str], n: int = 6) -> List[str]:
        seen, out = set(), []
        for s in items:
            k = s.lower()
            if k not in seen:
                seen.add(k)
                out.append(s)
            if len(out) >= n:
                break
        return out

    return _dedup_cap(pros), _dedup_cap(cons)


# ---------- Normalize one item ----------

def _normalize_item(item: Dict[str, Any]) -> Dict[str, Any]:
    title = _norm_str(item.get("title"))
    price_text = _norm_str(item.get("price") or item.get("price_text"))
    price_value = _to_int(item.get("price_value") or item.get("price") or item.get("price_text"))
    brand = _norm_str(item.get("brand"))
    cpu = _norm_str(item.get("cpu"))
    ram_gb = _to_int(item.get("ram_gb"))
    storage_ssd_gb = _to_int(item.get("storage_ssd_gb"))
    storage_hdd_gb = _to_int(item.get("storage_hdd_gb"))
    gpu = _norm_str(item.get("gpu"))
    os_val = _norm_str(item.get("os"))
    screen_inches = _to_float(item.get("screen_inches"))
    source = _norm_str(item.get("source") or item.get("site") or "web")
    link = _norm_str(item.get("link") or item.get("url"))
    image = _norm_str(item.get("image") or item.get("thumbnail") or item.get("img") or item.get("image_url"))

    row: Dict[str, Any] = {
        "title": title,
        "price": price_text,
        "price_value": price_value,
        "brand": brand,
        "cpu": cpu,
        "ram_gb": ram_gb if ram_gb is not None else "",
        "storage_ssd_gb": storage_ssd_gb if storage_ssd_gb is not None else "",
        "storage_hdd_gb": storage_hdd_gb if storage_hdd_gb is not None else "",
        "gpu": gpu,
        "os": os_val,
        "screen_inches": screen_inches if screen_inches is not None else "",
        "source": source,
        "link": link,
        "image": image,
    }

    # Enrich: why + score + pros/cons
    row["why_choose"] = _build_why_choose(row)
    pre = item.get("score")
    row["score"] = float(pre) if isinstance(pre, (int, float)) else _score_item(row)

    pros_list, cons_list = _build_pros_cons(row)
    row["pros_list"] = pros_list
    row["cons_list"] = cons_list
    # CSV-friendly strings
    row["pros"] = " • ".join(pros_list)
    row["cons"] = " • ".join(cons_list)

    return row


def _desired_columns() -> List[str]:
    # Put why/score/pros/cons near the top for readability
    return [
        "title",
        "price",
        "price_value",
        "why_choose",
        "score",
        "pros",
        "cons",
        "brand",
        "cpu",
        "ram_gb",
        "storage_ssd_gb",
        "storage_hdd_gb",
        "gpu",
        "os",
        "screen_inches",
        "source",
        "link",
        "image",
    ]


# ---------- Public API ----------

def save_results(run_dir: str, results: List[Dict[str, Any]]) -> Dict[str, str]:
    os.makedirs(run_dir, exist_ok=True)

    # Normalize + enrich
    normalized: List[Dict[str, Any]] = [_normalize_item(r or {}) for r in (results or [])]
    # Sort by score desc (then title)
    normalized.sort(key=lambda r: (r.get("score") or 0.0, _norm_str(r.get("title"))), reverse=True)

    cols = _desired_columns()

    # Paths
    csv_path = os.path.join(run_dir, "results.csv")
    json_path = os.path.join(run_dir, "results.json")

    # CSV (UTF-8 BOM)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for row in normalized:
            safe = {k: ("" if v is None else v) for k, v in row.items()}
            writer.writerow(safe)

    # JSON (pretty; keeps lists + strings)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)

    return {"csv": csv_path, "json": json_path}
