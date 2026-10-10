from app.live_pnl import calculate
def test_convergence_profit():
 x=calculate(1,100,110,104,106,1,1,-.5)
 assert x.long_gross==4 and x.short_gross==4 and x.net==5.5
