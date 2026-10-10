from app.replay_oos_metrics import metrics
from app.promotion_policy_v2 import evaluate
def test_oos_metrics_and_promotion_gate():
 m=metrics([{"net":1}]*100);assert m["oos"]["net"]>0;assert evaluate(m,0,min_oos=20)["eligible"]
