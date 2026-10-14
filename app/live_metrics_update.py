from dataclasses import replace
def on_open(metrics):return replace(metrics,entries=metrics.entries+1)
def on_close(metrics,net):return replace(metrics,closes=metrics.closes+1,realized_net=metrics.realized_net+float(net))
def on_recovery(metrics):return replace(metrics,recoveries=metrics.recoveries+1)
def on_incident(metrics,unknown=False):return replace(metrics,incidents=metrics.incidents+1,unknown_orders=metrics.unknown_orders+(1 if unknown else 0))
