from app.sizing import quantity_for_notional
def test_quantity():
    assert quantity_for_notional(10,3,.1,.1)==3.3
    assert quantity_for_notional(1,100,.1,.1) is None
