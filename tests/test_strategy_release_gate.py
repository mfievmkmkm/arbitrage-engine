from app.strategy_release_gate import evaluate
def test_spot_future_stays_live_locked_after_campaign():
 x=evaluate("spot_futures",True);assert x.scan and x.paper and not x.live
