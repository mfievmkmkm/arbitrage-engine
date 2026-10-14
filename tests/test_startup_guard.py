from app.startup_guard import evaluate_startup
class P:
 def __init__(self,symbol,side,qty):self.symbol=symbol;self.side=side;self.qty=qty
def test_startup_guard():
 assert evaluate_startup([],[]).safe
 assert not evaluate_startup([],["order"]).safe
 assert not evaluate_startup([P("BTC","long",1)],[]).safe
