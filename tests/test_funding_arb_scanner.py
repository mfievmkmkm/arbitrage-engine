from app.funding_arb_scanner import evaluate
def test_funding_arb_unknown_interval_blocks():
 assert not evaluate("a","b",.01,.1,None,8,.01).allowed
 x=evaluate("a","b",.01,.1,8,8,.01);assert x.allowed and x.long_venue=="a"
