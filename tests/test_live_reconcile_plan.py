from types import SimpleNamespace
from app.live_reconcile_plan import plan
def test_db_is_restart_authority():
 x=plan([{"trade_id":"a"}],[]);assert x[0]["action"]=="PRIVATE_RECONCILE"
 y=plan([], [SimpleNamespace(trade_id="b")]);assert y[0]["action"]=="GLOBAL_HALT"
