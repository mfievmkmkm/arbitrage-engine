from app.spot_future_execution_plan import build
from app.spot_future_close_plan import build as close
def test_close_reverses_spot_and_reduce_only_future():
 op={"direction":"LONG_SPOT_SHORT_FUTURE","base_qty":1,"spot_symbol":"X/USDT","future_symbol":"X/USDT:USDT"};p=build(op);c=close(p);assert p.spot.side=="buy" and p.future.side=="sell" and c.spot.side=="sell" and c.future.side=="buy" and c.future.reduce_only
