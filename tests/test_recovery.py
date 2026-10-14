from app.recovery import recovery_action,restart_action
def test_recovery():
    assert recovery_action(1,0,.2,.01,.03)=="COMPLETE"
    assert recovery_action(1,0,-.1,.01,.03)=="FLATTEN"
    assert restart_action(False,False)=="NO_NEW_TRADES"
    assert restart_action(True,True)=="RECONCILE_AND_FLATTEN"
