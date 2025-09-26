from typing import Any, Dict, List, Tuple
from app.browser.controller import BrowserController
from app.skills.search import ddg_search, bing_search
from app.skills.extractors.flipkart import extract_flipkart_products
from app.skills.extractors.amazon import extract_amazon_products
from app.utils.io import make_run_dir
from app.utils.logger import logger

SUPPORTED_ENGINES = {"duckduckgo": ddg_search, "bing": bing_search}

def execute_plan(plan: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    steps = plan.get("plan", [])
    run_dir = make_run_dir()
    artifacts: Dict[str, Any] = {"screenshots": []}
    results: List[Dict[str, Any]] = []

    with BrowserController(run_dir=run_dir) as bc:
        for i, step in enumerate(steps, start=1):
            action = step.get("action")

            if action == "web_search":
                engine = step.get("engine", "duckduckgo").lower()
                query = step.get("query")
                max_results = int(step.get("max_results", 5))
                fn = SUPPORTED_ENGINES.get(engine, ddg_search)
                page_results = fn(bc, query, max_results)

                # NEW: tag source so UI can render a badge
                for r in page_results:
                    r.setdefault("source", engine)

                results.extend(page_results)
                artifacts["screenshots"].append(bc.screenshot(f"step{i}_{engine}_results.png"))

            elif action == "extract_products":
                site = step.get("site", "").lower()
                query = step.get("query", "")
                max_results = int(step.get("max_results", 5))
                max_price = step.get("max_price", None)

                source = "unknown"
                if "flipkart" in site:
                    page_results = extract_flipkart_products(bc, max_results, query, max_price)
                    source = "flipkart"
                elif "amazon" in site:
                    page_results = extract_amazon_products(bc, max_results, query, max_price)
                    source = "amazon"
                else:
                    logger.warning(f"Unsupported site: {site}")
                    page_results = []

                # NEW: tag source on each product
                for r in page_results:
                    r.setdefault("source", source)

                results.extend(page_results)
                artifacts["screenshots"].append(bc.screenshot(f"step{i}_{source}_products.png"))

            else:
                logger.warning(f"Unsupported action: {action}")

    artifacts["run_dir"] = run_dir
    return results, artifacts
