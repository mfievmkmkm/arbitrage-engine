from types import SimpleNamespace
from app.live_entry_commit import commit
from app.executor_plan import build
from app.runtime_store import RuntimeStore

def test_runtime_commit_only_after_verified_entry(tmp_path):
 p=build("X","a","b",.04,.01,.01,lambda x:x,lambda x:x)
 store=RuntimeStore(str(tmp_path/"runtime.json"))
 actual=SimpleNamespace(base_qty=.04,long_price=100,short_price=110,long_fee=.01,short_fee=.02)
 bad=SimpleNamespace(opened=False,actual=actual,trade_id="bad")
 assert not commit(store,[],bad,p,"X","a","b",1).committed
 assert store.load()==[]
 good=SimpleNamespace(opened=True,actual=actual,trade_id="good",entry=SimpleNamespace(long_result=SimpleNamespace(filled=4),short_result=SimpleNamespace(filled=4)))
 r=commit(store,[],good,p,"X","a","b",1)
 assert r.committed and store.load()[0].trade_id=="good"
