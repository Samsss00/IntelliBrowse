# app/agent/planner.py
from app.utils.logger import logger
from app.utils.money import extract_budget_and_clean_query

def plan_from_query(query: str, max_results: int = 5) -> dict:
    logger.info(f"Generating plan for query: {query}")

    site = "flipkart"
    qlow = (query or "").lower()
    if "amazon" in qlow:
        site = "amazon"
    elif "flipkart" in qlow:
        site = "flipkart"

    cleaned_query, max_price = extract_budget_and_clean_query(query)

    return {
        "plan": [
            {
                "action": "extract_products",
                "site": site,
                "max_results": max_results,
                "query": cleaned_query,   # e.g., "laptops under 50000"
                "max_price": max_price    # e.g., 50000
            }
        ]
    }

# Backwards compatibility
generate_plan = plan_from_query
