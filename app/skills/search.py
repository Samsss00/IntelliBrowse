# app/skills/search.py
from typing import List, Dict
from urllib.parse import quote_plus
from app.browser.controller import BrowserController
from app.utils.logger import logger

def _collect_links(bc: BrowserController, item_selector: str, max_results: int = 5) -> List[Dict]:
    assert bc.page
    items = bc.page.locator(item_selector)
    n = min(items.count(), max_results)
    out: List[Dict] = []
    for i in range(n):
        el = items.nth(i)
        href = el.get_attribute("href")
        title = (el.text_content() or "").strip()
        out.append({"rank": i + 1, "title": title, "link": href})
    return out

def ddg_search(bc: BrowserController, query: str, max_results: int = 5) -> List[Dict]:
    url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
    bc.goto(url)
    try:
        bc.page.wait_for_selector("a.result__a", timeout=15000)
        return _collect_links(bc, "a.result__a", max_results)
    except Exception:
        logger.warning("DuckDuckGo selector failed; fallback")
        return _collect_links(bc, "a", max_results)

def bing_search(bc: BrowserController, query: str, max_results: int = 5) -> List[Dict]:
    url = f"https://www.bing.com/search?q={quote_plus(query)}"
    bc.goto(url)
    try:
        bc.page.wait_for_selector("li.b_algo h2 a", timeout=15000)
        return _collect_links(bc, "li.b_algo h2 a", max_results)
    except Exception:
        logger.warning("Bing selector failed; fallback")
        return _collect_links(bc, "h2 a", max_results)
