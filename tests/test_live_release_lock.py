from app.live_release_lock import allowed
def test_release_lock_requires_every_switch():
 assert allowed(True,True,True,True)
 assert not allowed(True,True,True,False)
