from app.live_arm_token import issue
def test_arm_requires_all_evidence():
 assert issue(True,True,True,False).valid
 assert not issue(True,False,True,False).valid
