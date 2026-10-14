from app.live_incident_policy import classify
def test_incident_policy_prioritizes_unknown_state():
 assert classify("SUBMIT_UNKNOWN_RECONCILE").halt
 assert classify("EXIT_PARTIAL").action=="PROTECTIVE_FLATTEN"
 assert classify("MARKET_DATA_STALE").action=="BLOCK_NEW_ENTRY"
