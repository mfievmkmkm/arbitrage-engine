from app.strategy_paper_coordinator import Coordinator
from app.spot_future_paper_engine import Engine
def test_coordinator_opens_spot_future_paper():
 op={"base":"X","exchange":"a","direction":"LONG_SPOT_SHORT_FUTURE","notional":100,"base_qty":1,"prices":{"spot_buy":100,"spot_sell":99,"future_buy":110,"future_sell":109},"fee_pct":0,"safety_pct":0,"funding_pct":0,"hypothetical_edge":3};o,c=Coordinator(Engine()).spot_future([op],2);assert len(o)==1
