from app.startup_runtime import evaluate
def test_unresolved_intent_blocks():
 x=evaluate([],{},False,{"i":"SUBMITTED"})
 assert not x.safe and x.reason=="UNRESOLVED_ORDER_INTENTS"
