from app.auto_gate import evaluate
def test_auto_requires_live_history_and_zero_incidents():
 assert evaluate(50,1.4,1,0,False,True).allowed
 assert not evaluate(49,2,0,0,False,True).allowed
 assert not evaluate(100,2,0,1,False,True).allowed
