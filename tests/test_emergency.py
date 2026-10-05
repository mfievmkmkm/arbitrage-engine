from app.emergency import plan
def test_flatten_direction():
 assert plan("x","BTC",2).side=="sell"
 x=plan("x","BTC",-2);assert x.side=="buy" and x.reduce_only
 assert plan("x","BTC",0) is None
