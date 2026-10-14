from app.contract_math import normalize,balanced
def test_different_contract_sizes_balance():
 a=normalize(10,.001);b=normalize(1,.01)
 assert a.base_amount==b.base_amount
 assert balanced(a,b)
