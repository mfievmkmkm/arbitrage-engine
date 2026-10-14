from app.live_drawdown import check
def test_equity_drawdown_halts_at_limit():
 assert not check(50,49.5,2).halted
 assert check(50,49,2).halted
