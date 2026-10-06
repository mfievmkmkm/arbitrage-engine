from app.spot_future_paper_engine import Engine


def test_spot_future_paper_opens_and_marks():
    e = Engine(capital=500)
    op = {
        "base": "X",
        "exchange": "a",
        "direction": "LONG_SPOT_SHORT_FUTURE",
        "notional": 100,
        "base_qty": 1,
        "prices": {
            "spot_buy": 100,
            "spot_sell": 99.9,
            "future_buy": 110.1,
            "future_sell": 110,
        },
        "fee_pct": 0,
        "safety_pct": 0,
        "funding_pct": 0,
    }
    assert e.open(op)
    assert len(e.positions) == 1
