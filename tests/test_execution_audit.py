from app.execution_audit import trade
class X:long_error="";short_error="RuntimeError";hedged=False
def test_audit_detects_partial():assert not trade(X()).ok
