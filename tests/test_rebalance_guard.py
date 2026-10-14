from app.rebalance_plan import recommend
from app.rebalance_guard import executable
def test_rebalance_is_recommendation_only():
 p=recommend({"a":0},{"a":10});assert p and p[0]["action"]=="DEPOSIT_MANUALLY" and not executable(p)
