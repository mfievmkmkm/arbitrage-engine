from types import SimpleNamespace
from app.live_trade_commit import remove_verified
from app.runtime_store import RuntimeStore
from app.runtime_state import RuntimeTrade
def test_runtime_trade_removed_only_after_private_verified_close(tmp_path):
 s=RuntimeStore(str(tmp_path/"r.json"));t=RuntimeTrade("t","X","a","b",1,1,1,1,1,100,110,0);s.save([t])
 bad=SimpleNamespace(closed=False,status="CLOSE_UNVERIFIED_RESIDUAL_EXPOSURE")
 assert not remove_verified(s,[t],"t",bad).removed and len(s.load())==1
 good=SimpleNamespace(closed=True,status="CLOSED_VERIFIED")
 assert remove_verified(s,[t],"t",good).removed and s.load()==[]
