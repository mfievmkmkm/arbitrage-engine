from app.spot_spot_gate import check
def test_spot_spot_requires_prepositioned_inventory():
 assert check(0,2,200,100,1).allowed
 assert not check(0,0,200,100,1).allowed
 assert not check(0,2,200,100,1,True).allowed
