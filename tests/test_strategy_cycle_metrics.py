from app.strategy_cycle_metrics import Metrics
def test_strategy_metrics_track_failures():
 x=Metrics();x.success(4,10);x.fail();assert x.cycles==2 and x.observations==4 and x.success_pct==50
