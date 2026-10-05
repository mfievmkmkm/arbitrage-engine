from app.instruments import InstrumentSpec,compatible,min_notional_ok
def spec(name,cost_min,contract_size=1.0,amount_min=None):
 return InstrumentSpec(name,"BTC/USDT:USDT","BTC","USDT","USDT",True,True,amount_min,cost_min,None,contract_size)
def test_identity_and_minimum():
 a=spec("a",5);b=spec("b",10)
 assert compatible(a,b)
 assert min_notional_ok(a,5)
 assert not min_notional_ok(b,5)
def test_contract_derived_minimum():
 a=spec("a",None,.001,10)
 assert min_notional_ok(a,5,500)
 assert not min_notional_ok(a,4,500)
