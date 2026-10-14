from app.live_scale_gate import evaluate
def test_scale_up_requires_history_and_small_step():
 assert not evaluate(5,6,5).allowed
 assert evaluate(5,6,20).allowed
 assert not evaluate(5,10,100).allowed
