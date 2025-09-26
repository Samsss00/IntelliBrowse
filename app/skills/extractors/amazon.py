# app/skills/extractors/amazon.py
from typing import List, Dict, Optional
from urllib.parse import quote_plus
from app.utils.logger import logger
from app.browser.controller import BrowserController
from app.utils.money import parse_price_to_int

def extract_amazon_products(
    bc: BrowserController,
    max_results: int = 5,
    query: Optional[str] = None,
    max_price: Optional[int] = None
) -> List[Dict]:
    results: List[Dict] = []
    q = quote_plus(query or "laptops under 50000")
    search_url = f"https://www.amazon.in/s?k={q}"

    logger.info(f"Navigating Amazon with URL: {search_url}")
    bc.goto(search_url, timeout=60000)

    try:
        bc.page.wait_for_selector("div.s-main-slot div[data-component-type='s-search-result']", timeout=25000)
        items = bc.page.query_selector_all("div.s-main-slot div[data-component-type='s-search-result']")
    except Exception as e:
        logger.error(f"Amazon selector failed: {e}")
        return results

    parsed: List[Dict] = []
    for item in items:
        try:
            title = item.query_selector("h2 a span")
            link = item.query_selector("h2 a")
            price_whole = item.query_selector("span.a-price-whole")
            price_fraction = item.query_selector("span.a-price-fraction")

            price_text = None
            if price_whole:
                price_text = price_whole.inner_text().strip()
                if price_fraction:
                    price_text += "." + price_fraction.inner_text().strip()
                price_text = "₹" + price_text

            price_int = parse_price_to_int(price_text or "")

            if price_int is None:
                continue
            if max_price is not None and price_int > max_price:
                continue

            parsed.append({
                "title": title.inner_text().strip() if title else "N/A",
                "price": price_text or "N/A",
                "price_value": price_int,
                "link": "https://www.amazon.in" + link.get_attribute("href") if link else "N/A"
            })
        except Exception as e:
            logger.warning(f"Amazon item parse failed: {e}")

    parsed.sort(key=lambda x: x.get("price_value", 10**9))
    return parsed[:max_results]
