from app.dex_gas_budget import check
def test_gas_cannot_consume_too_much_edge():
 assert check(.2,1,.25).allowed
 assert not check(.5,1,.25).allowed
