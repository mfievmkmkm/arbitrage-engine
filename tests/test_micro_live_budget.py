from app.micro_live_budget import build,allowed
def test_small_bank():
 b=build(50)
 assert b.max_trade_loss==.25 and b.daily_loss==1 and b.max_notional==5 and b.max_open_trades==1
 assert allowed(b,6,0)[0] is False
