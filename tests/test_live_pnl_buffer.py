from app.live_pnl import calculate
def test_safety_buffer_is_not_reported_as_fee():
 x=calculate(1,100,110,104,106,1,2.1,-.5,.25)
 assert x.fees==3.1 and x.safety_buffer==.25 and abs(x.net-4.15)<1e-12
