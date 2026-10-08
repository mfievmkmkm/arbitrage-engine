from app.live_control_view import render
from app.live_supervisor import LiveSupervisor
from app.operator_stop import StopController


def test_control_view_exposes_lock_reasons():
    x = render(LiveSupervisor(), [], 0, StopController())
    assert "заблокирована" in x and "Блокировки:" in x
