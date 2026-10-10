from app.micro_live_runtime import MicroLiveRuntime
from app.live_coordinator import LiveCoordinator
from app.live_supervisor import LiveSupervisor
def ready():
 s=LiveSupervisor();s.ci_green=s.fee_verified=s.funding_known=s.book_fresh=s.private_verified=s.restart_clean=s.closed_e2e=True;s.unknown_orders=False;return s
def test_runtime_blocks_stale_market_before_submit():
 r=MicroLiveRuntime(LiveCoordinator(ready(),50));caps={"client_id":True,"reduce_only":True,"private_positions":True}
 market={"now_ts":10,"long_book_ts":1,"short_book_ts":10,"funding_known":True,"funding_usd":0,"max_funding_cost":.1,"long_ref":100,"short_ref":110,"long_fill":None,"short_fill":None,"max_slippage_pct":.2}
 x=r.before_entry("X","a","b",0,0,True,caps,caps,market);assert not x.allowed and "MARKET_DATA_STALE" in x.reasons
