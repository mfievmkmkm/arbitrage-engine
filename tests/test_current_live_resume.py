from types import SimpleNamespace as NS
import pytest
from app.resume_evidence import collect_current
from app.live_commands import resume
from app.operator_stop import StopController
from app.kill_switch import KillSwitch


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "stale",
        "future",
        "nan",
        "bool",
        "private",
        "reconciled",
        "unknown",
        "text_unknown",
        "supervisor",
    ],
)
def test_resume_cannot_use_stale_or_unverified_monitor_state(change):
    supervisor = NS(restart_clean=True, private_verified=True, unknown_orders=False)
    summary = dict(ts=1000, reconciled=True, private_verified=True, unknown_orders=0)
    if change == "missing":
        summary = None
    if change == "stale":
        summary["ts"] = 984
    if change == "future":
        summary["ts"] = 1001
    if change == "nan":
        summary["ts"] = float("nan")
    if change == "bool":
        summary["ts"] = True
    if change == "private":
        summary["private_verified"] = False
    if change == "reconciled":
        summary["reconciled"] = False
    if change == "unknown":
        summary["unknown_orders"] = 1
    if change == "text_unknown":
        summary["unknown_orders"] = "не проверено"
    if change == "supervisor":
        supervisor.restart_clean = False
    stop = StopController(True, "OPERATOR_STOP")
    result = resume(stop, collect_current(supervisor, summary, 1000), KillSwitch())
    assert not result.allowed and stop.stopped


def test_explicit_resume_can_clear_stop_with_current_reconciled_state():
    supervisor = NS(restart_clean=True, private_verified=True, unknown_orders=False)
    summary = dict(ts=1000, reconciled=True, private_verified=True, unknown_orders=0)
    stop = StopController(True, "OPERATOR_STOP")
    result = resume(stop, collect_current(supervisor, summary, 1000), KillSwitch())
    assert result.allowed and not stop.stopped


def test_current_snapshot_does_not_clear_active_kill_switch():
    supervisor = NS(restart_clean=True, private_verified=True, unknown_orders=False)
    summary = dict(ts=1000, reconciled=True, private_verified=True, unknown_orders=0)
    stop = StopController(True, "OPERATOR_STOP")
    kill = KillSwitch()
    kill.trip_global("UNRESOLVED")
    assert (
        not resume(stop, collect_current(supervisor, summary, 1000), kill).allowed
        and stop.stopped
    )
