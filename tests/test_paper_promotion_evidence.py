from app.paper_promotion_evidence import evaluate
def test_promotion_requires_oos_and_clean_incidents():
 assert evaluate(100,1.3,1,1,0).allowed
 assert not evaluate(100,1.3,-1,1,0).allowed
 assert not evaluate(100,1.3,1,1,1).allowed
