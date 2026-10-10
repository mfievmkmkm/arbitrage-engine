from app.dex_live_authority import allowed
def test_dex_live_is_hard_locked():assert not allowed(True,True,True,True)
