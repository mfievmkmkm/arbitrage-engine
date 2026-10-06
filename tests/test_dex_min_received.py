from app.dex_min_received import calculate
def test_min_received_applies_slippage():assert calculate(100,1)==99
