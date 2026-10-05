from app.safety_matrix import evaluate
def test_live_requires_everything():
 assert not evaluate(True,True,True,True,True,True,False).live
 assert evaluate(True,True,True,True,True,True,True).live
 assert not evaluate(False,True,True,True,True,True,True).observation
