from app.utils.money import parse_price_to_int, extract_budget_and_clean_query

def test_parse_price():
    assert parse_price_to_int("₹49,990") == 49990
    assert parse_price_to_int("63,989.00") == 63989
    assert parse_price_to_int("N/A") is None

def test_budget_parser():
    q, b = extract_budget_and_clean_query("Find top 5 laptops under 50k on Flipkart")
    assert b == 50000
    assert "laptops" in q and "flipkart" not in q.lower()
