from app.recovery_executor import plan
def test_complete_missing_short():
 x=plan("a","b",.01,.005,1,.01,.02)
 assert x.action=="COMPLETE" and x.venue=="b" and x.base_amount==.005
def test_flatten_long():
 x=plan("a","b",.01,.005,-1,.01,.02)
 assert x.action=="FLATTEN" and x.venue=="b" and x.side=="sell"
