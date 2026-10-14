from app.execution_sim import hedge_sim
def test_partial():
    r=hedge_sim([[10,1]],[[11,.5]],1)
    assert not r["complete"]
    assert r["unhedged_qty"]==.5
