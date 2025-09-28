# app/skills/extractors/amazon.py
from __future__ import annotations

import os, re, time, logging
from typing import List, Dict, Optional
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)

AMAZON_ROOT = "https://www.amazon.in"
AMZN_BUDGET_MS = int(os.getenv("AMZN_BUDGET_MS", "25000"))  # extractor-local time budget

def _deadline_ms() -> int:
    return int(time.time() * 1000) + AMZN_BUDGET_MS

def _remaining_ms(deadline: int) -> int:
    return max(0, deadline - int(time.time() * 1000))

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

def _visible_text(loc) -> str:
    try:
        return (loc.inner_text() or "").strip()
    except Exception:
        return ""

def _has_captcha(page) -> bool:
    try:
        if page.locator("#captchacharacters").count() > 0:
            return True
        if page.get_by_text("Enter the characters you see below").count() > 0:
            return True
        if page.get_by_text("To discuss automated access to Amazon data").count() > 0:
            return True
    except Exception:
        pass
    return False

def _dismiss_consent(page):
    try:
        btn = page.get_by_role("button", name=re.compile(r"(accept|agree|consent|ok)", re.I))
        if btn and btn.count() > 0:
            btn.first.click(timeout=800)
            page.wait_for_timeout(300)
    except Exception:
        pass

def _scroll(page, times=4, delay_ms=350, deadline: Optional[int] = None):
    for _ in range(times):
        if deadline is not None and _remaining_ms(deadline) < 50:
            break
        try:
            page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
        except Exception:
            break
        page.wait_for_timeout(min(delay_ms, _remaining_ms(deadline) if deadline else delay_ms))

def _url_query(query: str, max_price: Optional[int]) -> str:
    rh = f"&rh=p_36%3A-{max_price*100}" if max_price else ""
    # force Computers category if needed: &i=computers
    return f"{AMAZON_ROOT}/s?k={quote_plus(query)}&s=price-asc-rank{rh}"

def _url_laptops_cat(max_price: Optional[int]) -> str:
    # Laptops category node on amazon.in (commonly 1375424031)
    rh = "n%3A1375424031"
    if max_price:
        rh += f"%2Cp_36%3A-{max_price*100}"
    return f"{AMAZON_ROOT}/s?i=computers&rh={rh}&s=price-asc-rank&k=laptop"

def _url_computers_cat(query: str, max_price: Optional[int]) -> str:
    rh = "n%3A976392031"  # Computers & Accessories
    if max_price:
        rh += f"%2Cp_36%3A-{max_price*100}"
    return f"{AMAZON_ROOT}/s?i=computers&rh={rh}&s=price-asc-rank&k={quote_plus(query)}"

def _find_cards(page):
    # Try several selectors; return the first with elements.
    selectors = [
        'div.s-main-slot [data-asin][data-component-type="s-search-result"]',
        'div[data-asin].s-result-item',
        'div.s-card-container',
        'div[data-cel-widget^="search_result_"]',
    ]
    for sel in selectors:
        loc = page.locator(sel)
        if loc.count() > 0:
            return loc
    # last-chance XPath: h2 anchors inside search results
    loc = page.locator('//div[contains(@class,"s-result-item")]//h2/a')
    return loc if loc.count() > 0 else page.locator("")

def extract_amazon_products(
    bc,
    max_results: int = 5,
    query: Optional[str] = None,
    max_price: Optional[int] = None,
) -> List[Dict]:
    results: List[Dict] = []
    deadline = _deadline_ms()

    try:
        bc.page.set_default_timeout(6000)
        bc.page.set_default_navigation_timeout(12000)
    except Exception:
        pass

    q = (query or "").strip() or "laptops under 50000"

    urls = [
        _url_query(q, max_price),
        _url_laptops_cat(max_price),
        _url_computers_cat(q, max_price),
    ]

    cards = None
    for idx, url in enumerate(urls, start=1):
        if _remaining_ms(deadline) < 600:
            break

        logger.info(f"Amazon navigate (variant {idx}): {url}")
        try:
            bc.goto(url, timeout=min(18_000, _remaining_ms(deadline)))
        except Exception as e:
            logger.warning(f"Amazon goto failed (variant {idx}): {e}")
            continue

        if _has_captcha(bc.page):
            logger.error("Amazon human verification wall detected.")
            return results

        _dismiss_consent(bc.page)
        bc.page.wait_for_timeout(min(700, _remaining_ms(deadline)))
        _scroll(bc.page, times=3, delay_ms=320, deadline=deadline)

        probe = _find_cards(bc.page)
        if probe.count() > 0:
            cards = probe
            break

    if not cards or cards.count() == 0:
        logger.warning("Amazon: no result cards visible within budget/variants.")
        return results

    limit = min(max_results, cards.count())
    for i in range(limit):
        if _remaining_ms(deadline) < 50:
            break

        item = cards.nth(i)

        # Skip Sponsored if detectable
        try:
            if item.get_by_text(re.compile(r"\bSponsored\b", re.I)).count() > 0:
                continue
        except Exception:
            pass

        title_txt, href, price_txt, price_val = "", "", "", None

        # Title + link (primary + fallback)
        try:
            link_el = item.locator("h2 a").first
            title_el = item.locator("h2 a span").first
            if (not link_el or link_el.count() == 0) or (not title_el or title_el.count() == 0):
                title_el = item.locator(".a-size-base-plus.a-text-normal").first
                if title_el and title_el.count() > 0:
                    link_el = title_el.locator("xpath=ancestor::a[1]")

            if title_el and title_el.count() > 0:
                title_txt = _visible_text(title_el)
            if link_el and link_el.count() > 0:
                href = (link_el.get_attribute("href") or "").strip()
                if href.startswith("/"):
                    href = AMAZON_ROOT + href
        except Exception:
            pass

        # Price
        try:
            p = item.locator(".a-price .a-offscreen").first
            if p and p.count() > 0:
                price_txt = _visible_text(p)
            else:
                whole = _visible_text(item.locator("span.a-price-whole").first)
                frac  = _visible_text(item.locator("span.a-price-fraction").first)
                if whole:
                    price_txt = whole if not frac else f"{whole}.{frac}"
        except Exception:
            pass

        price_val = _norm_price(price_txt)

        # Respect budget if provided
        if max_price is not None and price_val is not None and price_val > max_price:
            continue

        if not title_txt and not href:
            continue

        results.append({
            "title": title_txt or "N/A",
            "price": price_txt or "N/A",
            "price_value": price_val,
            "link": href or "N/A",
            "source": "amazon",
        })

        if len(results) >= max_results:
            break

    return results
