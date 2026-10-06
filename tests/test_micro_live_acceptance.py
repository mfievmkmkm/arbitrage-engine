from app.micro_live_acceptance import evaluate
def test_acceptance_requires_every_safety_check():
 keys=("ci","compile","unit","entry_e2e","exit_e2e","restart_e2e","unknown_order","private_reconcile","kill_switch","daily_stop","fee_verified","funding_known","withdraw_safe","venue_capabilities")
 assert evaluate({k:True for k in keys}).passed
 assert not evaluate({k:True for k in keys if k!="unknown_order"}).passed
