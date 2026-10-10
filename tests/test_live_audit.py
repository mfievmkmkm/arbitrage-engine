from app.live_audit import incident
def test_unknown_state_is_critical_audit_event():
 assert incident("ORDER_UNKNOWN","t").severity=="CRITICAL"
