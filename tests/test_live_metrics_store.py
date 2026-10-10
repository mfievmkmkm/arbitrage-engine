from app.live_metrics import LiveMetrics
from app.live_metrics_store import LiveMetricsStore
def test_metrics_survive_restart(tmp_path):
 s=LiveMetricsStore(str(tmp_path/"m.json"));s.save(LiveMetrics(entries=2,realized_net=.4));x=s.load(LiveMetrics);assert x.entries==2 and x.realized_net==.4
