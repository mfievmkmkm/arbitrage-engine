from app.execution_fees import liquidity,resolve
from app.exchange_executor import SubmitRequest
from app.fee_schedule import FeeSchedule,FeeRate

def test_market_and_ioc_are_taker_limit_is_maker():
 assert liquidity("market")=="taker"
 assert liquidity("limit",True)=="taker"
 assert liquidity("limit")=="taker"
 assert liquidity("post_only")=="maker"

def test_execution_requests_select_venue_fee_rates():
 s=FeeSchedule({"a":FeeRate(.001,.002),"b":FeeRate(.003,.004)})
 x=resolve("a","b",SubmitRequest("X","buy",1,"limit"),SubmitRequest("X","sell",1,"market"),s)
 assert (x.long_rate,x.short_rate,x.long_liquidity,x.short_liquidity)==(.001,.004,"maker","taker")
