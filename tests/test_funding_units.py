from app.funding_units import to_pct
def test_ccxt_fraction_rate_converts_to_pct():assert to_pct(.0001)==.01
