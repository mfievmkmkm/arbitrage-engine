from app.dex_safety import check
def test_dex_unknown_contract_or_gas_blocks():
 assert check(True,True,True,True,True).allowed
 assert not check(False,True,False,True,True).allowed
