from app.live_metrics import LiveMetrics
from app.live_metrics_update import on_open,on_close,on_recovery,on_incident
def test_metrics_lifecycle_updates():
 x=on_open(LiveMetrics());x=on_recovery(x);x=on_incident(x,True);x=on_close(x,.5)
 assert (x.entries,x.closes,x.recoveries,x.incidents,x.unknown_orders,x.realized_net)==(1,1,1,1,1,.5)
