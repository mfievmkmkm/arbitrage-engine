from app.order_policy import choose
def test_policy():
 assert choose(2,.02,True).ioc
 assert choose(2,.2,False).order_type=="none"
 assert choose(2,.2,True,True).order_type=="market"
