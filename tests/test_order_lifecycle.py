from app.order_lifecycle import transition,restart_action
def test_lifecycle():
 assert transition("PLANNED","SUBMITTING").allowed
 assert transition("SUBMITTED","FILLED").allowed
 assert not transition("FILLED","SUBMITTED").allowed
 assert restart_action("SUBMITTED")=="RECONCILE_EXCHANGE"
