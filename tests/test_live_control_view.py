from app.live_control_view import render
from app.live_supervisor import LiveSupervisor
from app.operator_stop import StopController


def test_control_view_exposes_lock_reasons():
    x = render(LiveSupervisor(), [], 0, StopController())
    assert "заблокирована" in x and "Блокировки:" in x


def test_configured_entry_displays_latest_guard_reason_without_claiming_readiness():
    x = render(
        LiveSupervisor(),
        [],
        0,
        StopController(),
        entry_configured=True,
        entry_status={"status": "ENTRY_MARGIN_BUFFER_LOW <venue>"},
    )
    assert "настроены" in x and "ENTRY_MARGIN_BUFFER_LOW &lt;venue&gt;" in x
    assert "перед каждой отправкой" in x and "Проверки наблюдателя" in x
