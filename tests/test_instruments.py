from app.instruments import InstrumentSpec,compatible,min_notional_ok
def test_identity_and_minimum():
    a=InstrumentSpec("a","BTC/USDT:USDT","BTC","USDT","USDT",True,True,None,5,None)
    b=InstrumentSpec("b","BTC/USDT:USDT","BTC","USDT","USDT",True,True,None,10,None)
    assert compatible(a,b)
    assert min_notional_ok(a,5)
    assert not min_notional_ok(b,5)
