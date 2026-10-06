from app.shutdown_safety import evaluate
def test_shutdown_never_ignores_unknown_orders():
 assert not evaluate(0,True).safe
 assert evaluate(1,False).action=="PERSIST_AND_MONITOR_ON_RESTART"
