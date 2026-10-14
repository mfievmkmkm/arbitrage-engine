from app.net_edge import calculate
def test_unknown_funding_not_implicitly_verified():
 x={"funding_known":False,"funding_status":"UNKNOWN"}
 assert not x["funding_known"]
 assert calculate(2,.4,0,.1).net_pct==1.5
