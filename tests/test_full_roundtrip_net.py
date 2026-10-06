from app.full_roundtrip_net import calculate
def test_net_includes_entry_exit_slippage_funding_and_safety():
 x=calculate(2,.1,.1,-.05,.05,.05,.1);assert abs(x.net_pct-1.55)<1e-9 and abs(x.cost_pct-.4)<1e-9
