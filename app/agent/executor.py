# app/agent/executor.py
from __future__ import annotations
from typing import Any, Dict, List, Tuple
import re
import time
import json
import os

from app.browser.controller import BrowserController
from app.skills.search import ddg_search, bing_search
from app.skills.extractors.flipkart import extract_flipkart_products
from app.skills.extractors.amazon import extract_amazon_products  # kept for future use
from app.skills.extractors.reliance import extract_reliance_products  # present but parked
from app.utils.io import make_run_dir
from app.utils.logger import logger
from app.utils.cache import SimpleFileCache
from app.config.settings import RUNS_DIR

SUPPORTED_ENGINES = {"duckduckgo": ddg_search, "bing": bing_search}

# Keep Reliance/Croma mapped to Flipkart for stability right now
SITE_REMAP = {
    "amazon": "flipkart",
    "croma": "flipkart",
    "reliance digital": "flipkart",
    "reliance": "flipkart",
}

PRICE_NUM_RE = re.compile(r"(\d[\d,]*)", re.I)

# --- Spec parsing regexes ---
RE_SCREEN = re.compile(r"(\d{2}(?:\.\d)?)\s*(?:\"|inches|inch|\-inch|\s?in)\b", re.I)
RE_RAM = re.compile(r"\b(\d{1,2})\s*gb\s*ram\b|\b(\d{1,2})\s*gb\b", re.I)
RE_SSD = re.compile(r"\b(4|8|16|32|64|128|256|512|1024|2048)\s*gb\s*(?:ssd|nvme|m\.2)\b|\b(1|2)\s*tb\s*(?:ssd|nvme)\b", re.I)
RE_HDD = re.compile(r"\b(500)\s*gb\s*hdd\b|\b(1|2)\s*tb\s*hdd\b", re.I)
RE_GPU = re.compile(r"\b(rtx\s*\d{3,4}0|gtx\s*\d{3,4}0|arc\s*\w+|radeon\s*(rx\s*)?\w+)\b", re.I)
RE_CPU_GEN = re.compile(r"\b(i3|i5|i7|i9)\b|\b(ryzen)\s*(3|5|7|9)\b|\b(athlon|celeron|pentium)\b", re.I)
RE_CPU_DETAIL = re.compile(r"\b(i[3579]-?\d{3,5}[a-zA-Z]?\w*|ryzen\s*[3-9]\s*\d{3,5}[a-z]?\w*|r\d\s*\d{3,5}\w*|intel\s*core\s*i[3579]|ryzen\s*[3-9])\b", re.I)
RE_OS = re.compile(r"\bwindows\s*11|windows\s*10|win\s*11|win\s*10|dos|ubuntu|linux|chrome\s*os\b", re.I)

KNOWN_BRANDS = [
    "hp", "dell", "lenovo", "asus", "acer", "msi", "apple", "avita", "infinix",
    "lg", "samsung", "xiaomi", "realme", "honor", "nokia", "itel", "tecno",
    "mi", "vaio", "alienware", "victus", "omen", "redmibook"
]

# ---------------- Helpers ---------------- #

def _capture_error(
    bc: BrowserController,
    step_idx: int,
    label: str,
    artifacts: Dict[str, Any],
    err: Exception,
) -> None:
    try:
        snap = bc.screenshot(f"error_step{step_idx}_{label}.png")
        artifacts.setdefault("screenshots", []).append(snap)
        html = bc.save_html(f"error_step{step_idx}_{label}.html")
        artifacts["error_html"] = html
    except Exception:
        logger.exception("Failed to persist error artifacts")
    logger.exception(f"Step {step_idx} failed: {label}")
    artifacts["error"] = str(err)


def _capture_info(
    bc: BrowserController,
    step_idx: int,
    label: str,
    artifacts: Dict[str, Any],
) -> None:
    try:
        snap = bc.screenshot(f"step{step_idx}_{label}.png")
        artifacts.setdefault("screenshots", []).append(snap)
        html = bc.save_html(f"step{step_idx}_{label}.html")
        artifacts.setdefault("html", []).append(html)
    except Exception:
        pass


def _attach_source(items: List[Dict[str, Any]], source: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for it in items or []:
        if "source" not in it or not it.get("source"):
            it = {**it, "source": source}
        out.append(it)
    return out


def _parse_price_value(price_text: Any) -> int | None:
    if price_text is None:
        return None
    if isinstance(price_text, (int, float)):
        try:
            return int(price_text)
        except Exception:
            return None
    s = str(price_text)
    m = PRICE_NUM_RE.search(s)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except Exception:
        return None


def _ensure_price_value(items: List[Dict[str, Any]]) -> None:
    for it in items:
        if it.get("price_value") is None:
            it["price_value"] = _parse_price_value(it.get("price"))


def _filter_by_price_range(
    items: List[Dict[str, Any]], min_price: int | None, max_price: int | None
) -> List[Dict[str, Any]]:
    if min_price is None and max_price is None:
        return items
    out: List[Dict[str, Any]] = []
    for it in items:
        pv = it.get("price_value")
        if pv is None:
            out.append(it)  # keep unknowns
            continue
        if min_price is not None and pv < int(min_price):
            continue
        if max_price is not None and pv > int(max_price):
            continue
        out.append(it)
    return out


def _filter_by_keywords(
    items: List[Dict[str, Any]], include: List[str] | None, exclude: List[str] | None
) -> List[Dict[str, Any]]:
    if not include and not exclude:
        return items
    inc = [w.strip().lower() for w in (include or []) if w.strip()]
    exc = [w.strip().lower() for w in (exclude or []) if w.strip()]

    def ok(title: str) -> bool:
        t = (title or "").lower()
        if inc and not all(word in t for word in inc):
            return False
        if exc and any(word in t for word in exc):
            return False
        return True

    return [it for it in items if ok(it.get("title", ""))]


def _dedupe(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for it in items:
        key = (it.get("link") or "") + "|" + str(it.get("price_value") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _sort_results(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Price asc (unknowns at bottom), then title A–Z
    def keyf(it: Dict[str, Any]):
        pv = it.get("price_value")
        return (1 if pv is None else 0, pv if pv is not None else 10**12, (it.get("title") or "").lower())
    return sorted(items, key=keyf)


def _extract_brand(title: str) -> str | None:
    t = (title or "").lower()
    for b in KNOWN_BRANDS:
        if re.search(rf"\b{re.escape(b)}\b", t):
            return b
    first = (t.split() or [""])[0]
    if first and first.isalpha() and len(first) <= 12:
        return first
    return None


def _gb_to_int(text_num: str | None) -> int | None:
    if not text_num:
        return None
    try:
        return int(text_num)
    except Exception:
        return None


def _tb_to_gb(text_num: str | None) -> int | None:
    if not text_num:
        return None
    try:
        return int(text_num) * 1024
    except Exception:
        return None


def _parse_specs_from_title(title: str) -> Dict[str, Any]:
    t = title or ""
    specs: Dict[str, Any] = {}

    m = RE_SCREEN.search(t)
    if m:
        try:
            specs["screen_inches"] = float(m.group(1))
        except Exception:
            pass

    m = RE_RAM.search(t)
    if m:
        ram = _gb_to_int(m.group(1) or m.group(2))
        if ram:
            specs["ram_gb"] = ram

    m = RE_SSD.search(t)
    if m:
        if m.group(1):
            specs["storage_ssd_gb"] = _gb_to_int(m.group(1))
        elif m.group(2):
            specs["storage_ssd_gb"] = _tb_to_gb(m.group(2))

    m = RE_HDD.search(t)
    if m:
        if m.group(1):
            specs["storage_hdd_gb"] = _gb_to_int(m.group(1))
        elif m.group(2):
            specs["storage_hdd_gb"] = _tb_to_gb(m.group(2))

    m = RE_GPU.search(t)
    if m:
        specs["gpu"] = m.group(0).upper().replace("  ", " ").strip()

    cpu_model = None
    m = RE_CPU_DETAIL.search(t)
    if m:
        cpu_model = m.group(0)
    else:
        m = RE_CPU_GEN.search(t)
        if m:
            cpu_model = " ".join(g for g in m.groups() if g)
    if cpu_model:
        cpu_model = cpu_model.upper().replace("  ", " ").strip()
        specs["cpu"] = cpu_model
        if cpu_model.startswith("I") or cpu_model.startswith("INTEL"):
            specs["cpu_brand"] = "intel"
        elif "RYZEN" in cpu_model or "RADEON" in cpu_model or "ATHLON" in cpu_model:
            specs["cpu_brand"] = "amd"

    m = RE_OS.search(t)
    if m:
        os_name = m.group(0).lower().replace("win", "windows")
        os_name = os_name.replace("  ", " ").strip()
        specs["os"] = os_name

    brand = _extract_brand(t)
    if brand:
        specs["brand"] = brand

    return specs


def _enrich_specs(items: List[Dict[str, Any]]) -> None:
    for it in items:
        title = it.get("title") or ""
        specs = _parse_specs_from_title(title)
        for k, v in specs.items():
            it.setdefault(k, v)


# ---------------- Reliability Boost: Retry Wrapper ---------------- #

def _retry_extract(
    fn,
    bc: BrowserController,
    *,
    max_results: int,
    query: str,
    max_price: int | None,
    retries: int = 3,
    backoff_base: float = 1.6,
    site_label: str = "flipkart",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    tries = max(1, int(retries))
    meta: Dict[str, Any] = {"attempts": 0, "attempt_durations": [], "last_error": None}
    results: List[Dict[str, Any]] = []

    for attempt in range(1, tries + 1):
        t0 = time.time()
        meta["attempts"] = attempt
        try:
            logger.info(f"[retry] {site_label} extract attempt {attempt}/{tries}")
            results = fn(bc, max_results=max_results, query=query, max_price=max_price)
            dt = round(time.time() - t0, 3)
            meta["attempt_durations"].append(dt)

            if results and len(results) > 0:
                logger.info(f"[retry] {site_label} attempt {attempt}: {len(results)} items")
                return results, meta

            if attempt < tries:
                delay = round(backoff_base ** attempt, 2)
                logger.warning(f"[retry] {site_label} attempt {attempt} returned 0 items; backing off {delay}s")
                time.sleep(min(delay, 6.0))
                try:
                    if getattr(bc, "page", None):
                        bc.page.reload()
                        bc.page.wait_for_timeout(600)
                except Exception:
                    pass
                continue

            logger.warning(f"[retry] {site_label} attempts exhausted with 0 items")

        except Exception as e:
            dt = round(time.time() - t0, 3)
            meta["attempt_durations"].append(dt)
            meta["last_error"] = str(e)
            logger.exception(f"[retry] {site_label} attempt {attempt} error: {e}")
            if attempt < tries:
                delay = round(backoff_base ** attempt, 2)
                time.sleep(min(delay, 6.0))
                try:
                    if getattr(bc, "page", None):
                        bc.page.reload()
                        bc.page.wait_for_timeout(600)
                except Exception:
                    pass
                continue

    return results, meta


# ---------------- Cache Key ---------------- #

def _plan_cache_key(site: str, query: str, max_results: int, max_price: Any, min_price: Any, include: Any, exclude: Any) -> str:
    """
    Deterministic key from the extraction inputs (order-insensitive for include/exclude).
    """
    norm = {
        "site": site,
        "query": query,
        "max_results": int(max_results),
        "max_price": max_price if max_price is None else int(max_price),
        "min_price": min_price if min_price is None else int(min_price),
        "include": sorted([s.strip().lower() for s in (include or []) if str(s).strip()]) if include else [],
        "exclude": sorted([s.strip().lower() for s in (exclude or []) if str(s).strip()]) if exclude else [],
    }
    return json.dumps(norm, sort_keys=True, ensure_ascii=False)


# ---------------- Main Execute ---------------- #

def execute_plan(plan: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Execute plan steps.
    Supported step:
      - extract_products: {site, query, max_results, max_price, min_price?, include?, exclude?}
    """
    run_dir = make_run_dir()
    artifacts: Dict[str, Any] = {"run_dir": run_dir, "screenshots": [], "steps": []}
    all_results: List[Dict[str, Any]] = []

    # Cache Setup (TTL configurable via env; default 12h)
    cache_dir = os.path.join(RUNS_DIR, "_cache")
    ttl_sec = int(os.getenv("CACHE_TTL_SEC", "43200"))
    cache = SimpleFileCache(cache_dir, ttl_sec=ttl_sec)

    logger.info(f"Executing plan with {len(plan.get('plan', []))} step(s)")

    # Currently single-step; loop kept for future multi-step
    with BrowserController(run_dir=run_dir) as bc:
        for i, step in enumerate(plan.get("plan", []), start=1):
            action = step.get("action", "")
            label = action or f"step{i}"
            try:
                if action == "extract_products":
                    site_raw = (step.get("site") or "flipkart").lower().strip()
                    site = SITE_REMAP.get(site_raw, site_raw)

                    query = step.get("query") or ""
                    max_results = int(step.get("max_results") or 5)
                    max_price = step.get("max_price")
                    min_price = step.get("min_price")
                    include_words = step.get("include") or []
                    exclude_words = step.get("exclude") or []

                    if isinstance(include_words, str):
                        include_words = [w for w in include_words.split(",") if w.strip()]
                    if isinstance(exclude_words, str):
                        exclude_words = [w for w in exclude_words.split(",") if w.strip()]

                    logger.info(
                        f"[executor] extract_products: site={site} query={query!r} "
                        f"max_results={max_results} price_range=({min_price},{max_price}) "
                        f"include={include_words} exclude={exclude_words}"
                    )

                    # ---------- Cache check ----------
                    ck = _plan_cache_key(site, query, max_results, max_price, min_price, include_words, exclude_words)
                    cached = cache.get(ck)
                    if cached:
                        # Rehydrate and return immediately
                        page_results = list(cached)  # type: ignore
                        _ensure_price_value(page_results)
                        _enrich_specs(page_results)
                        page_results = _filter_by_price_range(page_results, min_price, max_price)
                        page_results = _filter_by_keywords(page_results, include_words, exclude_words)
                        page_results = _dedupe(page_results)
                        page_results = _sort_results(page_results)
                        if len(page_results) > max_results:
                            page_results = page_results[:max_results]

                        artifacts["steps"].append(
                            {
                                "action": action, "site": site,
                                "status": "cached", "count": len(page_results),
                                "cache_ttl_sec": ttl_sec,
                            }
                        )
                        all_results.extend(_attach_source(page_results, site))
                        # don't early-return; allow future multi-steps
                        continue

                    # ---- Live extraction with retries ----
                    if site == "flipkart":
                        page_results, meta = _retry_extract(
                            extract_flipkart_products, bc,
                            max_results=max_results, query=query, max_price=max_price,
                            retries=3, backoff_base=1.6, site_label="flipkart"
                        )
                    elif site == "amazon":
                        page_results, meta = _retry_extract(
                            extract_amazon_products, bc,
                            max_results=max_results, query=query, max_price=max_price,
                            retries=2, backoff_base=1.8, site_label="amazon"
                        )
                    elif site == "reliance":
                        # Parked → Flipkart for now
                        page_results, meta = _retry_extract(
                            extract_flipkart_products, bc,
                            max_results=max_results, query=query, max_price=max_price,
                            retries=3, backoff_base=1.6, site_label="flipkart(remap)"
                        )
                        site = "flipkart"
                    else:
                        logger.warning(f"Unsupported site: {site}")
                        page_results, meta = [], {"attempts": 1, "attempt_durations": [], "last_error": "unsupported site"}

                    # --- Post-processing pipeline ---
                    page_results = _attach_source(page_results, site)
                    _ensure_price_value(page_results)
                    _enrich_specs(page_results)  # structured specs from title
                    page_results = _filter_by_price_range(page_results, min_price, max_price)
                    page_results = _filter_by_keywords(page_results, include_words, exclude_words)
                    page_results = _dedupe(page_results)
                    page_results = _sort_results(page_results)
                    if len(page_results) > max_results:
                        page_results = page_results[:max_results]
                    # --------------------------------

                    # Save to cache (store minimal fields to keep size modest)
                    try:
                        cache.set(ck, page_results)
                    except Exception:
                        pass

                    _capture_info(bc, i, f"{site}_products", artifacts)
                    artifacts["steps"].append(
                        {
                            "action": action,
                            "site": site,
                            "status": "ok" if page_results else ("partial" if meta.get("attempts", 0) > 1 else "ok"),
                            "count": len(page_results),
                            "attempts": meta.get("attempts"),
                            "durations": meta.get("attempt_durations"),
                            "last_error": meta.get("last_error"),
                            "cached": False,
                        }
                    )
                    all_results.extend(page_results)

                else:
                    logger.warning(f"Unknown action: {action}")
                    artifacts["steps"].append(
                        {"action": action, "status": "skipped", "reason": "unknown action"}
                    )
                    _capture_info(bc, i, f"unknown_{i}", artifacts)

            except Exception as e:
                _capture_error(bc, i, label, artifacts, e)
                artifacts["steps"].append(
                    {"action": action, "status": "error", "error": str(e)}
                )
                # continue

        try:
            if getattr(bc, "page", None):
                artifacts["last_url"] = bc.page.url
        except Exception:
            pass

    return all_results, artifacts
 