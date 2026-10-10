from app.spot_future_symbols import normalize
def test_normalizes_common_spot_future_base():
 m={"s":{"symbol":"BTC/USDT","base":"BTC","quote":"USDT","spot":True,"active":True},"f":{"symbol":"BTC/USDT:USDT","base":"BTC","quote":"USDT","swap":True,"linear":True,"settle":"USDT","active":True}};x=normalize(m);assert len(x)==1 and x[0].base=="BTC"
