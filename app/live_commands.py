from dataclasses import dataclass


@dataclass(frozen=True)
class LiveCommand:
    allowed: bool
    action: str
    reason: str


def stop(controller):
    controller.stop("OPERATOR_STOP")
    return LiveCommand(True, "STOP", "OPERATOR_STOP")


def resume(controller, evidence, kill):
    if not evidence.safe:
        return LiveCommand(False, "RESUME", ";".join(evidence.reasons))
    if kill.check("", "", "").blocked:
        return LiveCommand(False, "RESUME", "KILL_SWITCH_ACTIVE")
    from .persistent_stop import Stop

    if isinstance(controller, Stop):
        controller.resume(evidence)
    else:
        controller.resume()
    return LiveCommand(True, "RESUME", "RESUME_ALLOWED")


def clear_monitor_kill(supervisor, summary, now):
    """Explicit operator action; fresh clean evidence, STOP remains unchanged."""
    from .resume_evidence import collect_current

    evidence = collect_current(supervisor, summary, now)
    if not evidence.safe:
        return LiveCommand(False, "CLEAR_KILL", ";".join(evidence.reasons))
    if any(
        x.get("severity") in ("CRITICAL", "HIGH")
        for x in (summary or {}).get("incidents", [])
    ):
        return LiveCommand(False, "CLEAR_KILL", "ACTIVE_EXECUTION_INCIDENT")
    reason = supervisor.kill.global_reason
    if not reason or not (
        reason.startswith("LIVE_MONITOR:") or reason == "LIVE_MONITOR_FAILURE"
    ):
        return LiveCommand(False, "CLEAR_KILL", "KILL_SCOPE_NOT_MONITOR")
    supervisor.kill.clear_global()
    return LiveCommand(True, "CLEAR_KILL", "MONITOR_KILL_CLEARED_STOP_UNCHANGED")
