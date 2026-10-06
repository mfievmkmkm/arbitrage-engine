from app.pnl_attribution import calculate
def test_pnl_attribution_reconciles_net():
 assert calculate(10,2,1,1,.5).net==7.5
