from app.reconcile import reconcile
class P:
 def __init__(self,symbol,side,qty):self.symbol=symbol;self.side=side;self.qty=qty
def test_unexpected_symbol_fails_expected_state():
 r=reconcile([P("ETH","long",1)],{"BTC":0})
 assert not r.trusted and r.reason=="POSITION_MISMATCH"
