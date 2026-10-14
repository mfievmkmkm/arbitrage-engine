from app.preflight import check
class C:
 token="x";admin_id=1;notional=5;live_enabled=False;paper_capital=50
def test_preflight():assert check(C()).ok
