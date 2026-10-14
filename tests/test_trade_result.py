from app.trade_result import finalize
def test_final():
 x=finalize("t",1,100,110,104,106,1,1,0,100,"TARGET")
 assert x.net==6 and x.roi_pct==6
