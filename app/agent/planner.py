# app/agent/planner.py
from __future__ import annotations
import re
from typing import Any, Dict

from app.utils.logger import logger
from app.utils.money import extract_budget_and_clean_query

SITE_WORDS = {"amazon", "flipkart", "croma", "reliance", "reliance digital"}


def _strip_amounts(q: str) -> str:
    q = re.sub(r"\b(under|below|less than)\s+\d[\d,]*k?\b", " ", q, flags=re.I)
    q = re.sub(r"\b\d[\d,]*k?\s*(budget|max|price)\b", " ", q, flags=re.I)
    return q


def _sanitize_free_query(query: str) -> str:
    q = (query or "").lower().strip()
    q = _strip_amounts(q)
    q = re.sub(r"\bon\s+(amazon|flipkart|croma|reliance|reliance digital)\b", " ", q, flags=re.I)
    for w in SITE_WORDS:
        q = re.sub(rf"\b{re.escape(w)}\b", " ", q, flags=re.I)
    q = re.sub(r"\btop\s+\d+\b|\bfind\s+\d+\b|^\s*\d+\s+", " ", q, flags=re.I)
    q = re.sub(r"\b(find|show|best|top|buy)\b", " ", q, flags=re.I)
    q = re.sub(r"\s+", " ", q).strip()
    return q or "laptops"


def _choose_site(qlow: str) -> str:
    site = "flipkart"
    if "reliance digital" in qlow or re.search(r"\breliance\b", qlow):
        site = "reliance"
    elif "flipkart" in qlow:
        site = "flipkart"
    elif "amazon" in qlow:
        site = "amazon"
    elif "croma" in qlow:
        site = "croma"
    return site


def plan_from_query(query: str, max_results: int = 5) -> Dict[str, Any]:
    logger.info(f"Generating plan for query: {query}")
    qlow = (query or "").lower()

    site = _choose_site(qlow)

    cleaned_query, max_price = extract_budget_and_clean_query(query)
    final_query = _sanitize_free_query(cleaned_query or query)

    plan: Dict[str, Any] = {
        "plan": [
            {
                "action": "extract_products",
                "site": site,
                "query": final_query,          # e.g., 'laptops'
                "max_results": int(max_results or 5),
                "max_price": max_price,        # e.g., 50000
            }
        ]
    }

    logger.info(f"Plan: {plan}")
    return plan
