from app.startup_runtime import evaluate
class T:symbol="X"
def test_runtime_trade_without_private_is_unsafe():
 x=evaluate([T()],{},False)
 assert not x.safe and x.mode=="OBSERVATION" and x.reason=="RUNTIME_TRADES_WITHOUT_PRIVATE_STATE"
