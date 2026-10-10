from app.funding_arb_economics import calculate
def test_funding_economics_includes_basis_and_full_costs():
 x=calculate(-.1,.2,8,8,8,.1,.02,.01);assert x.allowed and abs(x.net_pct-.17)<1e-9
