from app.rebalance_policy import decide
def test_rebalance_never_auto_enables_withdrawals():
 assert decide(True,False).reason=="MANUAL_REBALANCE_REQUIRED"
 assert not decide(True,True).automatic
