# app/utils/money.py
import re
from typing import Optional, Tuple

def parse_price_to_int(price_text: str) -> Optional[int]:
    """
    Convert strings like "₹49,990", "49,990.00" to 49990 (int).
    """
    if not price_text:
        return None
    txt = price_text.strip().lower()
    # keep digits , . and spaces; strip everything else (₹, Rs, etc.)
    txt = re.sub(r'[^\dk,.\s]', '', txt).replace(' ', '')
    m = re.search(r'(\d[\d,]*)(?:\.(\d{1,2}))?$', txt)
    if not m:
        return None
    whole = m.group(1).replace(',', '')
    try:
        return int(whole)
    except:
        return None


def _parse_budget_from_text(q: str) -> Tuple[Optional[int], bool]:
    """
    Returns (budget, found_k_suffix)
    - Detects patterns like '50k', '50 k', '50.5k' → 50000 / 50500
    - Or plain numbers like '₹50,000', '50000'
    """
    # Prefer explicit k-suffix first
    m_k = re.search(r'(\d+(?:\.\d+)?)\s*k\b', q, flags=re.IGNORECASE)
    if m_k:
        try:
            val = float(m_k.group(1))
            return int(val * 1000), True
        except:
            pass

    # Then plain amount with or without currency
    m_amt = re.search(r'[₹rs]?\s*([\d][\d,]{3,})', q, flags=re.IGNORECASE)
    if m_amt:
        try:
            return int(m_amt.group(1).replace(',', '')), False
        except:
            pass

    return None, False


def extract_budget_and_clean_query(query: str) -> tuple[str, Optional[int]]:
    """
    - Extract a numeric max budget (supports 'k' suffix).
    - Remove filler & site words (e.g., 'on Flipkart', 'find', 'top 5', etc.).
    - Return (cleaned_query_for_store, max_price_int or None).
    """
    if not query:
        return "laptops", None

    q = query.strip()

    # 1) Get budget
    budget, had_k = _parse_budget_from_text(q)

    # 2) Remove site words and filler phrases
    #    also remove comparative budget phrases so we can re-append a clean "under N"
    patterns_to_strip = [
        r'\bon\s+(flipkart|amazon)\b',
        r'\bfind\b',
        r'\bbest\b',
        r'\btop\s*\d+\b',
        r'\bunder\s*[₹rs]?\s*\d[\d,]*\s*k?\b',
        r'\bbelow\s*[₹rs]?\s*\d[\d,]*\s*k?\b',
        r'\bless\s*than\s*[₹rs]?\s*\d[\d,]*\s*k?\b',
        r'\b<=\s*[₹rs]?\s*\d[\d,]*\s*k?\b',
    ]
    cleaned = q
    for p in patterns_to_strip:
        cleaned = re.sub(p, '', cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    # 3) Ensure "laptops" keyword present
    if "laptop" not in cleaned.lower():
        cleaned = ("laptops " + cleaned).strip()

    # 4) If we found a budget, append a normalized "under {budget}"
    if budget:
        cleaned = f"{cleaned} under {budget}"

    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned, budget
