# app/skills/extractors/reliance.py
from __future__ import annotations
from typing import List, Dict, Optional
import re
from urllib.parse import quote_plus

from app.utils.logger import logger

PRICE_RE = re.compile(r"(\d[\d,]*)")


def _parse_price(text: str | None) -> Optional[int]:
    if not text:
        return None
    t = text.replace("\u00A0", " ").replace("\u20b9", "₹")
    m = PRICE_RE.search(t)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except Exception:
        return None


_SAN_SITE = re.compile(r"\bon\s+(reliance digital|reliance)\b", re.I)
_SAN_SITES = re.compile(r"\b(amazon|flipkart|croma|reliance digital|reliance)\b", re.I)
_SAN_COUNTS = re.compile(r"\btop\s+\d+\b|\bfind\s+\d+\b|^\s*\d+\s+", re.I)
_SAN_BUDGET = re.compile(r"\b(under|below|less than)\s+\d[\d,]*k?\b", re.I)
_SAN_BUDGET2 = re.compile(r"\b\d[\d,]*k?\s*(budget|max|price)\b", re.I)
_SAN_FILLERS = re.compile(r"\b(find|show|best|top|buy)\b", re.I)
_SAN_SPACES = re.compile(r"\s+")


def _clean_query(q: str) -> str:
    """Make a Reliance-friendly on-site query from noisy NLP text."""
    if not q:
        return "laptops"
    q = q.strip().lower()
    q = _SAN_SITE.sub(" ", q)
    q = _SAN_SITES.sub(" ", q)
    q = _SAN_COUNTS.sub(" ", q)
    q = _SAN_BUDGET.sub(" ", q)
    q = _SAN_BUDGET2.sub(" ", q)
    q = _SAN_FILLERS.sub(" ", q)
    q = _SAN_SPACES.sub(" ", q).strip()
    return q or "laptops"


def _build_search_url(query: str) -> str:
    # Reliance Digital search accepts ?q=<terms>
    cq = _clean_query(query or "laptops")
    return f"https://www.reliancedigital.in/search?q={quote_plus(cq)}"


def _normalize_item_link(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    return f"https://www.reliancedigital.in{href}"


def _uniquify(items: List[Dict]) -> List[Dict]:
    seen = set()
    out = []
    for it in items:
        key = (it.get("link") or "") + "|" + str(it.get("price_value") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def extract_reliance_products(
    bc,
    max_results: int = 5,
    query: str = "laptops",
    max_price: Optional[int] = None,
) -> List[Dict]:
    """
    Extract product cards from Reliance Digital search results.
    Returns list[{title, price, price_value, link}]
    """
    results: List[Dict] = []
    url = _build_search_url(query)
    logger.info(f"[reliance] Navigating to: {url}")

    page = bc.page
    bc.goto(url, wait_until="domcontentloaded")

    # Wait for product anchors; Reliance sometimes lazy-loads.
    try:
        page.wait_for_selector("a[href*='/product/']", timeout=8000)
    except Exception:
        logger.warning("[reliance] product anchors not immediately visible")

    # Gentle sync scrolling to trigger lazy load
    try:
        for _ in range(8):
            page.mouse.wheel(0, 1400)
            page.wait_for_timeout(350)
    except Exception:
        pass

    # Collect candidate product anchors
    anchors = page.query_selector_all("a[href*='/product/']")
    logger.info(f"[reliance] candidate product anchors: {len(anchors)}")

    for a in anchors:
        if len(results) >= max_results:
            break
        try:
            href = a.get_attribute("href") or ""
            link = _normalize_item_link(href)
            if not link:
                continue

            # Title: prefer heading inside card/anchor
            title_el = a.query_selector("h2, h3, .sp__name, .pDp__name, .pl__pname, .product_name")
            title = (title_el.inner_text().strip() if title_el else a.inner_text().strip())
            title = re.sub(r"\s+", " ", title or "")
            if not title:
                continue

            # Price: within this anchor or up to 2 ancestors
            price_text = None
            price_el = a.query_selector(".price, .pdp__price, .pl__price, [class*='price'], span:has-text('₹')")
            if not price_el:
                parent = a
                for _ in range(2):
                    try:
                        parent = parent.evaluate_handle("e => e && e.parentElement").as_element()
                    except Exception:
                        parent = None
                    if not parent:
                        break
                    price_el = parent.query_selector(".price, .pdp__price, .pl__price, [class*='price'], span:has-text('₹')")
                    if price_el:
                        break
            if price_el:
                price_text = price_el.inner_text().strip()

            price_value = _parse_price(price_text)
            if max_price and price_value and price_value > max_price:
                continue

            results.append(
                {
                    "title": title,
                    "price": f"₹{price_value:,}" if price_value else (price_text or "N/A"),
                    "price_value": price_value,
                    "link": link,
                }
            )
        except Exception:
            # ignore errors on individual tiles
            continue

    results = _uniquify(results)
    logger.info(f"[reliance] extracted {len(results)} items")
    return results
