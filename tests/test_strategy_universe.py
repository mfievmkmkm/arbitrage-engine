from app.strategy_universe import common_spot_symbols
class C:
 def __init__(self,s):self.markets={x:{"symbol":x,"spot":True,"quote":"USDT","active":True} for x in s}
def test_spot_universe_requires_multiple_venues():assert common_spot_symbols({"a":C(["X/USDT","Y/USDT"]),"b":C(["X/USDT"])})==["X/USDT"]
