from types import SimpleNamespace
from app.tg_risk_center import render
from app.tg_system_center import render as system


def test_risk_center_surfaces_untrusted_state():
    r = SimpleNamespace(
        state=SimpleNamespace(halted=False, paper_daily_pnl=0, engine_errors=0)
    )
    s = SimpleNamespace(
        private_verified=False, restart_clean=False, unknown_orders=["x"]
    )
    assert "Требуется сверка" in render(r, s, SimpleNamespace(stopped=False))
