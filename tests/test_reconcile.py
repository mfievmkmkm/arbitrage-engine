from app.reconcile import Position,reconcile
def test_reconcile():
    assert reconcile([]).trusted
    r=reconcile([Position("BTC","long",1)])
    assert not r.trusted and r.reason=="UNEXPECTED_EXPOSURE"
    assert reconcile([Position("BTC","long",1)],{"BTC":1}).trusted
