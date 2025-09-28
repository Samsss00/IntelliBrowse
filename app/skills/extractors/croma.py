# app/skills/extractors/croma.py
from __future__ import annotations

import json, re
from typing import List, Dict, Optional
from urllib.parse import urlparse, quote_plus
from app.utils.logger import logger
from app.skills.search import ddg_search, bing_search

CROMA_HOSTS = {"www.croma.com", "croma.com"}

TITLE_SEL_CANDIDATES = [
    'h1[itemprop="name"]',
    "h1",
    'meta[property="og:title"]',
]

PRICE_SEL_CANDIDATES = [
    '[itemprop="price"]',
    '.price .amount',
    '.pdp__price .amount',
    '.product-price .amount',
    'meta[itemprop="price"]',
]


def _norm_price(text: str) -> Optional[int]:
    if text is None:
        return None
    if isinstance(text, (int, float)):
        try:
            return int(float(text))
        except Exception:
            return None
    t = re.sub(r"[^\d.]", "", str(text))
    if not t:
        return None
    try:
        return int(float(t))
    except Exception:
        return None


def _is_croma_product_url(url: str) -> bool:
    try:
        u = urlparse(url)
        if u.netloc.lower() not in CROMA_HOSTS:
            return False
        # Croma PDPs usually have /p/ in the path or ?sku=...
        return ("/p/" in u.path) or (u.path.rstrip("/") == "/p") or ("sku=" in (u.query or ""))
    except Exception:
        return False


def _parse_jsonld_price(page) -> Optional[int]:
    try:
        blocks = page.locator('script[type="application/ld+json"]')
        n = blocks.count()
        for i in range(n):
            raw = blocks.nth(i).inner_text()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue

            def scan(obj):
                if isinstance(obj, dict):
                    if "offers" in obj:
                        off = obj["offers"]
                        if isinstance(off, dict):
                            val = _norm_price(off.get("price"))
                            if val is not None:
                                return val
                        if isinstance(off, list):
                            for o in off:
                                val = _norm_price((o or {}).get("price"))
                                if val is not None:
                                    return val
                    val = _norm_price(obj.get("price"))
                    if val is not None:
                        return val
                    for v in obj.values():
                        out = scan(v)
                        if out is not None:
                            return out
                elif isinstance(obj, list):
                    for v in obj:
                        out = scan(v)
                        if out is not None:
                            return out
                return None

            price_val = scan(data)
            if price_val is not None:
                return price_val
    except Exception:
        pass
    return None


def _extract_meta(page, css: str) -> str:
    try:
        loc = page.locator(css).first
        if loc and loc.count() > 0:
            content = loc.get_attribute("content")
            if content:
                return content.strip()
    except Exception:
        pass
    return ""


def _extract_from_pdp(bc, url: str) -> Optional[Dict]:
    try:
        bc.goto(url, timeout=20_000)
        # Title
        title = ""
        for sel in TITLE_SEL_CANDIDATES:
            try:
                if sel.startswith("meta"):
                    title = _extract_meta(bc.page, sel)
                else:
                    title = (bc.page.locator(sel).first.inner_text() or "").strip()
                if title:
                    break
            except Exception:
                continue

        # Price
        price_val = _parse_jsonld_price(bc.page)
        price_txt = None
        if price_val is None:
            for sel in PRICE_SEL_CANDIDATES:
                try:
                    raw = _extract_meta(bc.page, sel) if sel.startswith("meta") else (bc.page.locator(sel).first.inner_text() or "").strip()
                    val = _norm_price(raw)
                    if val is not None:
                        price_val = val
                        price_txt = raw
                        break
                except Exception:
                    continue

        if price_txt is None and price_val is not None:
            price_txt = f"₹{price_val:,}"

        if not title:
            return None

        return {
            "title": title,
            "price": price_txt or "N/A",
            "price_value": price_val,
            "link": url,
            "source": "croma",
        }
    except Exception as e:
        logger.debug(f"Croma PDP parse failed {url}: {e}")
        return None


def _normalize_query_core(q: str) -> str:
    """
    Make sure the query is product-only (e.g., 'laptops'), not '8 laptops on croma under 50000'.
    """
    q = q.lower()
    q = re.sub(r"\bon\s+croma\b", " ", q)
    q = re.sub(r"\bcroma\b", " ", q)
    q = re.sub(r"\b(top|find|show|get)\s+\d+\b", " ", q)
    q = re.sub(r"^\d+\s+", " ", q)
    q = re.sub(r"\bunder\s+\d+[kK]?\b", " ", q)
    q = re.sub(r"\b\d+[kK]\b", " ", q)
    q = re.sub(r"[^a-z0-9\s]+", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    if not q:
        return "laptops"
    return q


def _gather_croma_links(bc, query: str, want: int) -> List[str]:
    """
    Query the web engines for Croma PDPs.
    We build very *strict* queries to bias PDPs:
      - force 'site:croma.com'
      - force a product word like 'laptop'
      - try adding '/p/' indicator in the query string
    """
    core = _normalize_query_core(query)
    if "laptop" not in core:
        core += " laptop"

    # Prefer ddg HTML endpoint via our ddg_search wrapper
    ddg_qs = [
        f"site:croma.com {core} /p/",  # bias PDPs
        f"site:croma.com {core}",
    ]

    links: List[str] = []

    for ddg_q in ddg_qs:
        logger.info(f"Croma via DuckDuckGo: {ddg_q}")
        hints = ddg_search(bc, ddg_q, want * 4)
        for h in hints:
            u = (h.get("link") or h.get("url") or "").strip()
            if _is_croma_product_url(u):
                links.append(u)
        if links:
            break

    # If still nothing, try Bing with the same approach.
    if not links:
        bing_qs = [
            f"site:croma.com {core} /p/",
            f"site:croma.com {core}",
        ]
        for bq in bing_qs:
            logger.info(f"Croma via Bing: {bq}")
            hints = bing_search(bc, bq, want * 4)
            for h in hints:
                u = (h.get("link") or h.get("url") or "").strip()
                if _is_croma_product_url(u):
                    links.append(u)
            if links:
                break

    # Dedupe, keep order
    seen, out = set(), []
    for u in links:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out[: want * 3]


def extract_croma_products(
    bc,
    max_results: int = 5,
    query: Optional[str] = None,
    max_price: Optional[int] = None,
) -> List[Dict]:
    q = (query or "").strip() or "laptops"
    urls = _gather_croma_links(bc, q, max_results)

    out: List[Dict] = []
    for u in urls:
        if len(out) >= max_results:
            break
        row = _extract_from_pdp(bc, u)
        if not row:
            continue
        pv = row.get("price_value")
        if max_price is not None and pv is not None and pv > max_price:
            continue
        out.append(row)

    return out
