# app/skills/search.py
from __future__ import annotations

import re
from typing import List, Dict
from urllib.parse import quote_plus

from app.utils.logger import logger


def _dedupe(items: List[Dict]) -> List[Dict]:
    seen = set()
    out = []
    for x in items:
        k = (x.get("link") or "") + "|" + (x.get("title") or "")
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out


def ddg_search(bc, query: str, max_results: int = 5) -> List[Dict]:
    """
    DuckDuckGo HTML endpoint (no JS). Extremely stable in headless.
    """
    q = (query or "").strip()
    url = f"https://duckduckgo.com/html/?q={quote_plus(q)}"
    logger.info(f"DDG search: {url}")
    bc.goto(url, timeout=20_000, wait_until="domcontentloaded")

    # Two common selectors on the HTML endpoint:
    #  - 'a.result__a' (primary)
    #  - 'h2.result__title a.result__a' (older HTML)
    loc = bc.page.locator("a.result__a")
    if loc.count() == 0:
        loc = bc.page.locator("h2.result__title a.result__a")

    results: List[Dict] = []
    count = min(max_results * 2, loc.count())  # collect a bit more, then dedupe

    if count == 0:
        logger.warning("DuckDuckGo selector failed; no results found.")
        return results

    for i in range(count):
        a = loc.nth(i)
        try:
            title = (a.inner_text() or "").strip()
            href = (a.get_attribute("href") or "").strip()
            if not href:
                continue
            results.append({"title": title, "link": href, "source": "duckduckgo"})
            if len(results) >= max_results:
                break
        except Exception:
            continue

    return _dedupe(results)[:max_results]


def bing_search(bc, query: str, max_results: int = 5) -> List[Dict]:
    """
    Bing web search (public HTML). Reliable in headless with simple selectors.
    """
    q = (query or "").strip()
    url = f"https://www.bing.com/search?q={quote_plus(q)}"
    logger.info(f"Bing search: {url}")
    bc.goto(url, timeout=20_000, wait_until="domcontentloaded")

    # Typical Bing result anchors: 'li.b_algo h2 a'
    loc = bc.page.locator("li.b_algo h2 a")
    # Sometimes results appear inside '.b_ans' or '.b_algo h2' only; try fallback
    if loc.count() == 0:
        loc = bc.page.locator("h2 a")

    results: List[Dict] = []
    count = min(max_results * 2, loc.count())

    if count == 0:
        logger.warning("Bing selector failed; no results found.")
        return results

    for i in range(count):
        a = loc.nth(i)
        try:
            title = (a.inner_text() or "").strip()
            href = (a.get_attribute("href") or "").strip()
            if not href:
                continue
            results.append({"title": title, "link": href, "source": "bing"})
            if len(results) >= max_results:
                break
        except Exception:
            continue

    return _dedupe(results)[:max_results]
