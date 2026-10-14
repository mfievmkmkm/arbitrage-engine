from app.exit_policy import decide
def test_unified_exit_policy_prioritizes_safety():
 assert decide(1,2,1,10,.2,.7,.2,True).reason=="ADVERSE_MARKET"
 assert decide(1,2,1,10,.8,.7,.2).reason=="TARGET_CONVERGENCE"
