from app.replay_promotion import evaluate
def test_live_promotion_requires_out_of_sample_evidence():
 assert evaluate(100,1.3,.2,1).allowed
 assert not evaluate(99,2,.1,5).allowed
 assert not evaluate(100,2,.1,-1).allowed
