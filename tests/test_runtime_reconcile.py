from app.runtime_reconcile import reconcile
from app.runtime_state import RuntimeTrade
class P:
 def __init__(self,side,qty):self.symbol="X";self.side=side;self.qty=qty
class H:ok=True
def test_runtime_match():
 t=RuntimeTrade("1","X","a","b",1,1,1,1,1,10,11,1)
 s={"a":{"positions":[P("long",1)]},"b":{"positions":[P("short",1)]}}
 assert reconcile([t],s).trusted
