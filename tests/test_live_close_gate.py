from app.live_close_gate import confirm
class E:flat=True
def test_missing_private_not_closed():
 assert not confirm(E(),{},"X","a","b").closed
