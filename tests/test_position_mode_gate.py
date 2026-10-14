from app.position_mode_gate import check
def test_both_venues_need_known_compatible_mode():
 assert check("hedge","hedge").safe
 assert not check("oneway","hedge").safe
