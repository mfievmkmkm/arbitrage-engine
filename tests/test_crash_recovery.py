from app.crash_recovery import decide
class H:ok=True
class P:
 def __init__(self):self.symbol="BTC";self.side="long";self.qty=1
def test_unexpected_position():
 x=decide({"a":{"health":H(),"orders":[],"positions":[P()]}})
 assert not x.safe and x.action=="UNEXPECTED_EXPOSURE"
