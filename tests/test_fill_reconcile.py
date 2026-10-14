from app.fill_reconcile import reconcile
def test_contract_fill_exposure():
 x=reconcile(10,.001,1,.01)
 assert x.hedged and x.mismatch_base==0
 y=reconcile(5,.001,1,.01)
 assert not y.hedged
