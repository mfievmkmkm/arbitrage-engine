from app.live_resume import evaluate
def test_resume_is_fail_closed():
 assert evaluate(False,True,True,True,False).allowed
 assert not evaluate(False,True,True,True,True).allowed
