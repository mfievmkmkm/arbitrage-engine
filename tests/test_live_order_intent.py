from app.live_order_intent import OrderIntent,may_submit
def test_duplicate_requires_reconcile():
 i=OrderIntent("i","t","x","BTC","buy",1,False)
 assert may_submit(i,{})[0]
 assert may_submit(i,{"i":"SUBMITTED"})==(False,"RECONCILE_REQUIRED")
