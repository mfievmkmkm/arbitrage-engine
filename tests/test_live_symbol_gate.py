from app.live_symbol_gate import check
def test_symbol_must_be_active_on_both_venues():
 assert check(True,True,.001,.001).allowed
 assert not check(True,False,.001,.001).allowed
