from app.live_entry_admission import prepare
from app.executor_plan import build
from app.order_policy import OrderPolicy
from app.fee_schedule import FeeSchedule,FeeRate
class G:micro_live=True

def args():
 p=build("X","a","b",.0476,.0001,.0001,lambda x:x,lambda x:x)
 s=FeeSchedule({"a":FeeRate(.001,.002),"b":FeeRate(.001,.002)})
 return p,s

def test_post_cost_edge_blocks_trade():
 p,s=args();x=prepare(p,OrderPolicy("market",False,.1,"X"),100,101,s,1,True,G(),True,True,True,50)
 assert not x.allowed and "NET_EDGE_TOO_LOW" in x.reason

def test_unknown_fee_blocks_before_order_submission():
 p,_=args();x=prepare(p,OrderPolicy("market",False,.1,"X"),100,110,FeeSchedule({}),1,True,G(),True,True,True,50)
 assert not x.allowed and x.reason.startswith("UNKNOWN_FEE_RATE")

def test_good_net_edge_passes_full_admission():
 p,s=args();x=prepare(p,OrderPolicy("market",False,.1,"X"),100,110,s,1,True,G(),True,True,True,50)
 assert x.allowed and x.cost.net_edge_usd>1
