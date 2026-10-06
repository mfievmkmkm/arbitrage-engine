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
