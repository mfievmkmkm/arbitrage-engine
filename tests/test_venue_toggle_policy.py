from app.venue_toggle_policy import set_mode
def test_real_toggle_is_certification_gated():
 x,ok,r=set_mode({"real":False},"real",True,True,False);assert not ok and not x["real"]
 x,ok,r=set_mode(x,"real",True,True,True);assert ok and x["real"]
