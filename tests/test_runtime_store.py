from app.runtime_store import RuntimeStore
from app.runtime_state import RuntimeTrade
def test_roundtrip(tmp_path):
 p=tmp_path/"state.json";s=RuntimeStore(str(p));t=RuntimeTrade("1","X","a","b",1,1,1,1,1,10,11,1)
 s.save([t]);assert s.load()[0].trade_id=="1"
