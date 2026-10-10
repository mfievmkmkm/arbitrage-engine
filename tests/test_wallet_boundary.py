from app.wallet_boundary import check
def test_dex_wallet_must_be_isolated_and_limited():
 assert check(True,True,True).safe
 assert not check(False,True,True).safe
