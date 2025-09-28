# app/utils/score.py
from typing import Dict, List, Optional
import math

def _cpu_tier(cpu: Optional[str]) -> int:
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

def _has_discrete_gpu(gpu: Optional[str]) -> bool:
    s = (gpu or "").lower()
    return ("rtx" in s) or ("gtx" in s) or ("radeon" in s)

def _why_choose(row: Dict) -> str:
    bits: List[str] = []
    # price buckets
    price = row.get("price_value")
    try:
        price = float(price)
    except Exception:
        price = None
    if isinstance(price, (int, float)) and math.isfinite(price):
        if price < 35000:
            bits.append("great value")
        elif price < 50000:
            bits.append("good price")
        else:
            bits.append("premium tier")

    # RAM
    ram = row.get("ram_gb")
    try:
        ram = float(ram)
    except Exception:
        ram = None
    if isinstance(ram, (int, float)) and math.isfinite(ram):
        if ram >= 16:
            bits.append(f"{int(ram)}GB RAM")
        elif ram >= 8:
            bits.append("8GB RAM")

    # CPU
    cpu = (row.get("cpu") or "").strip()
    if cpu:
        nice = ["i9","i7","i5","i3","Ryzen 9","Ryzen 7","Ryzen 5","Ryzen 3","M3","M2","M1"]
        hit = next((t for t in nice if t.lower() in cpu.lower()), None)
        bits.append(hit or cpu.split(" ")[0])

    # SSD
    ssd = row.get("storage_ssd_gb")
    try:
        ssd = float(ssd)
    except Exception:
        ssd = None
    if isinstance(ssd, (int, float)) and math.isfinite(ssd):
        if ssd >= 512:
            bits.append("512GB+ SSD")
        elif ssd >= 256:
            bits.append("256GB SSD")

    # GPU
    if _has_discrete_gpu(row.get("gpu")):
        bits.append("discrete GPU")

    # OS
    if row.get("os"):
        bits.append(str(row["os"]).upper())

    # Screen
    scr = row.get("screen_inches")
    try:
        scr = float(scr)
    except Exception:
        scr = None
    if isinstance(scr, (int, float)) and math.isfinite(scr):
        bits.append('15"+ screen' if scr >= 15 else '~14" screen')

    # Brand
    if row.get("brand"):
        bits.append(row["brand"])

    # de-dup while preserving order
    seen = set()
    out: List[str] = []
    for b in bits:
        k = b.lower()
        if k not in seen:
            seen.add(k)
            out.append(b)
    return " • ".join(out[:6])

def _score_item(row: Dict, budget: Optional[float] = None) -> float:
    # weights (same spirit as UI)
    w = {
        "price": 0.35,
        "ram": 0.20,
        "cpu": 0.20,
        "ssd": 0.12,
        "gpu": 0.08,
        "screen": 0.03,
        "os": 0.02,
    }

    # price
    price = row.get("price_value")
    try:
        price = float(price)
    except Exception:
        price = float("inf")

    if isinstance(budget, (int, float)) and math.isfinite(budget) and budget > 0:
        if price <= budget:
            price_score = 1 - (price / max(budget, 1))
            price_score = min(1.0, price_score + 0.05)  # under-budget bonus
        else:
            price_score = max(0.0, 0.15 - (price - budget) / (budget * 2))
    else:
        # normalize roughly in 20k..150k
        price_score = max(0.0, min(1.0, (150000 - price) / 130000))

    # RAM
    ram = row.get("ram_gb")
    try:
        ram = float(ram)
    except Exception:
        ram = None
    if isinstance(ram, (int, float)) and math.isfinite(ram):
        if ram >= 32: ram_score = 1.0
        elif ram >= 16: ram_score = 0.8
        elif ram >= 8: ram_score = 0.5
        else: ram_score = 0.2
    else:
        ram_score = 0.0

    # CPU
    cpu_score = min(1.0, _cpu_tier(row.get("cpu")) / 5.0)

    # SSD
    ssd = row.get("storage_ssd_gb")
    try:
        ssd = float(ssd)
    except Exception:
        ssd = None
    if isinstance(ssd, (int, float)) and math.isfinite(ssd):
        if ssd >= 1024: ssd_score = 1.0
        elif ssd >= 512: ssd_score = 0.8
        elif ssd >= 256: ssd_score = 0.5
        else: ssd_score = 0.2
    else:
        ssd_score = 0.0

    # GPU
    gpu_score = 1.0 if _has_discrete_gpu(row.get("gpu")) else 0.3

    # Screen
    scr = row.get("screen_inches")
    try:
        scr = float(scr)
    except Exception:
        scr = None
    if isinstance(scr, (int, float)) and math.isfinite(scr):
        if 15 <= scr <= 16: screen_score = 1.0
        elif 13 <= scr < 15: screen_score = 0.8
        else: screen_score = 0.5
    else:
        screen_score = 0.6  # neutral default

    # OS
    os_ = str(row.get("os") or "").lower()
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

def enrich_results(rows: List[Dict], budget: Optional[float] = None) -> List[Dict]:
    """Return a NEW list with why_choose + score added, sorted by score desc."""
    out: List[Dict] = []
    for r in rows or []:
        rr = dict(r)  # shallow copy
        rr["why_choose"] = _why_choose(r)
        rr["score"] = _score_item(r, budget=budget)
        out.append(rr)
    # sort best first
    out.sort(key=lambda x: (x.get("score") or 0), reverse=True)
    return out
