from app.engine import Quote, vwap, evaluate

def test_vwap():
    assert vwap([[1.0, 2], [1.1, 2]], 3) == (2 + 1.1) / 3
    assert vwap([[1.0, 1]], 2) is None

def test_edge_and_freshness():
    a = Quote("binance", "ABC/USDT:USDT", [[0.99, 100]], [[1.0, 100]], 100)
    b = Quote("bybit", "ABC/USDT:USDT", [[1.1, 100]], [[1.11, 100]], 100)
    result = evaluate(a, b, 5, 10, now=101)
    assert result is not None
    assert round(result["raw"], 2) == 10
    assert result["hypothetical_edge"] < result["executable"]
    assert evaluate(a, b, 5, 10, now=200) is None

def test_depth_and_mismatch():
    a = Quote("binance", "ABC/USDT:USDT", [], [[1.0, 1]], 100)
    b = Quote("bybit", "ABC/USDT:USDT", [[1.1, 1]], [], 100)
    assert evaluate(a, b, 5, 10, now=100) is None
    assert evaluate(a, Quote("bybit", "OTHER/USDT:USDT", [[1.1, 10]], [], 100),
                    1, 10, now=100) is None
