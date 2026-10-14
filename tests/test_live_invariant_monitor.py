from types import SimpleNamespace
from app.live_invariant_monitor import evaluate
def test_runtime_without_durable_is_critical():assert "RUNTIME_WITHOUT_DURABLE" in evaluate([], [SimpleNamespace(trade_id="x")],[],True)["issues"]
