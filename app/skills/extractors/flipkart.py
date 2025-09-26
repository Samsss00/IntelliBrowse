# app/skills/extractors/flipkart.py
import time
from typing import List, Dict, Optional
from urllib.parse import quote_plus
from app.utils.logger import logger
from app.browser.controller import BrowserController
from app.utils.money import parse_price_to_int

def _dismiss_flipkart_login(bc: BrowserController):
    for sel in [
        "button._2KpZ6l._2doB4z",
        "button._2KpZ6l._2doB4z._3AWRsL",
        "button._2KpZ6l._2doB4z._1oVJPM",
        "img[alt='Close']",
        "button[aria-label='Close']",
        "._2KpZ6l._2doB4z"
    ]:
        try:
            bc.page.locator(sel).first.click(timeout=1000)
            logger.info("Closed Flipkart login/modal.")
            return
        except Exception:
            pass

def extract_flipkart_products(
    bc: BrowserController,
    max_results: int = 5,
    query: Optional[str] = None,
    max_price: Optional[int] = None
) -> List[Dict]:
    results: List[Dict] = []

    # Normalize a friendly search
    q = quote_plus(query or "laptops under 50000")
    url = f"https://www.flipkart.com/search?q={q}"
    logger.info(f"Navigating Flipkart with URL: {url}")
    bc.goto(url, timeout=60000)

    _dismiss_flipkart_login(bc)
    bc.page.wait_for_load_state("domcontentloaded")

    anchor_selectors = [
        "a.CGtC98",
        "div.tUxRFH a.CGtC98",
        "a.IRpwTa",
        "a._1fQZEK",
        "div._1AtVbE a.s1Q9rs"
    ]

    items = []
    for sel in anchor_selectors:
        try:
            bc.page.wait_for_selector(sel, timeout=8000)
            items = bc.page.query_selector_all(sel)
            if items:
                break
        except Exception:
            continue

    if not items:
        logger.info("Scrolling to force results load...")
        for _ in range(4):
            bc.page.mouse.wheel(0, 3000)
            time.sleep(1.0)
            try:
                items = bc.page.query_selector_all("a.CGtC98") or []
                if items:
                    break
            except Exception:
                pass

    if not items:
        logger.warning("No product anchors found on Flipkart.")
        return results

    parsed: List[Dict] = []
    for item in items:
        try:
            title_el = (
                item.query_selector("div.KzDlHZ")
                or item.query_selector("div._4rR01T")
                or item.query_selector("a.s1Q9rs")
            )
            price_el = (
                item.query_selector("div.Nx9bqj._4b5DiR")
                or item.query_selector("div._30jeq3._1_WHN1")
                or item.query_selector("div._30jeq3")
            )
            link = item.get_attribute("href")

            title = (title_el.inner_text().strip() if title_el else "N/A")
            price_text = (price_el.inner_text().strip() if price_el else "")
            price_int = parse_price_to_int(price_text)

            href = ("https://www.flipkart.com" + link) if (link and link.startswith("/")) else (link or "N/A")

            # Filter invalid/no-price items
            if price_int is None:
                continue

            # Apply budget filter if provided
            if max_price is not None and price_int > max_price:
                continue

            parsed.append({
                "title": title,
                "price": price_text or "N/A",
                "price_value": price_int,
                "link": href
            })
        except Exception as e:
            logger.warning(f"Flipkart item parse failed: {e}")

    # Sort by numeric price asc and slice
    parsed.sort(key=lambda x: x.get("price_value", 10**9))
    results = parsed[:max_results]

    if not results:
        logger.warning("No product items parsed after filtering on Flipkart")

    return results
