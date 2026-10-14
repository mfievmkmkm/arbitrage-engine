from .full_roundtrip_net import calculate
def prove(edge,fees_in,fees_out,funding,slip_in,slip_out,safety,min_net):
 x=calculate(edge,fees_in,fees_out,funding,slip_in,slip_out,safety);return {"allowed":x.net_pct>=min_net,"net_pct":x.net_pct,"cost_pct":x.cost_pct}
