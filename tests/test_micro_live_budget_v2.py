from app.micro_live_budget_v2 import allow
def test_micro_live_budget_caps_trade_and_daily_loss():assert not allow(50,0,10,0)[0] and not allow(50,0,2,2)[0] and allow(50,0,2,0)[0]
