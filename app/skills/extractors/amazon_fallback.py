# app/skills/extractors/amazon_fallback.py
from __future__ import annotations

import re
from typing import List, Dict, Optional
from urllib.parse import urlparse
from app.utils.logger import logger
from app.skills.search import ddg_search, bing_search

AMAZON_HOSTS = {"amazon.in", "www.amazon.in", "m.amazon.in"}

PRICE_SEL_PRIMARY = (
    "#corePriceDisplay_desktop_feature_div .a-offscreen, "
    "#corePrice_desktop .a-offscreen, "
    ".a-price .a-offscreen"
)
TITLE_SEL = "#productTitle"


def _norm_price(text: str) -> Optional[int]:
    if not text:
        return None
    t = re.sub(r"[^\d.]", "", text)
    if not t:
        return None
    try:
        return int(float(t))
    except Exception:
        return None


def _is_amazon_product_url(url: str) -> bool:
    try:
        u = urlparse(url)
        if u.netloc.lower() not in AMAZON_HOSTS:
            return False
        return ("/dp/" in u.path) or ("/gp/" in u.path) or ("/product/" in u.path)
    except Exception:
        return False


def _extract_from_product_page(bc, url: str) -> Optional[Dict]:
    try:
        bc.goto(url, timeout=18_000)
        title_loc = bc.page.locator(TITLE_SEL).first
        price_loc = bc.page.locator(PRICE_SEL_PRIMARY).first

        title = (title_loc.inner_text() or "").strip() if title_loc and title_loc.count() > 0 else ""
        price_txt = (price_loc.inner_text() or "").strip() if price_loc and price_loc.count() > 0 else ""
        price_val = _norm_price(price_txt)

        if not title:
            return None

        return {
            "title": title,
            "price": price_txt or "N/A",
            "price_value": price_val,
            "link": url,
            "source": "amazon",
        }
    except Exception as e:
        logger.debug(f"Amazon fallback: failed to parse product page {url}: {e}")
        return None


def _gather_amazon_links(bc, query: str, want: int) -> List[str]:
    """
    Try DDG first; if empty, try Bing. Return up to 'want' canonical product URLs.
    """
    ddg_q = f"site:amazon.in {query}"
    links: List[str] = []

    logger.info(f"Amazon fallback via DuckDuckGo: {ddg_q}")
    hints = ddg_search(bc, ddg_q, want * 3)
    for h in hints:
        u = h.get("link") or h.get("url") or ""
        if u and _is_amazon_product_url(u):
            links.append(u)

    if not links:
        logger.info("DDG yielded no amazon product links. Trying Bing...")
        bing_q = f"site:amazon.in {query}"
        hints = bing_search(bc, bing_q, want * 3)
        for h in hints:
            u = h.get("link") or h.get("url") or ""
            if u and _is_amazon_product_url(u):
                links.append(u)

    # Deduplicate while preserving order
    seen = set()
    uniq = []
    for u in links:
        if u in seen:
            continue
        seen.add(u)
        uniq.append(u)
    return uniq[: want * 2]  # still over-collect a bit (some PDPs fail)


def extract_amazon_products_fallback(
    bc,
    max_results: int = 5,
    query: Optional[str] = None,
    max_price: Optional[int] = None,
) -> List[Dict]:
    q = (query or "").strip() or "laptops under 50000"
    urls = _gather_amazon_links(bc, q, max_results)

    out: List[Dict] = []
    for url in urls:
        if len(out) >= max_results:
            break
        row = _extract_from_product_page(bc, url)
        if not row:
            continue
        pv = row.get("price_value")
        if max_price is not None and pv is not None and pv > max_price:
            continue
        out.append(row)

    return out
