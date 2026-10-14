from dataclasses import dataclass


@dataclass(frozen=True)
class LiveEvidence:
    ready: bool
    reasons: tuple


def evaluate(
    ci_green,
    private_verified,
    restart_clean,
    fee_verified,
    funding_known,
    book_fresh,
    kill_clear,
    unknown_orders,
    closed_e2e,
):
    reasons = []
    for ok, r in [
        (ci_green, "CI_RED"),
        (private_verified, "PRIVATE_UNVERIFIED"),
        (restart_clean, "RESTART_UNSAFE"),
        (fee_verified, "FEE_UNVERIFIED"),
        (funding_known, "FUNDING_UNKNOWN"),
        (book_fresh, "BOOK_STALE"),
        (kill_clear, "KILL_SWITCH"),
        (unknown_orders is False, "UNKNOWN_ORDERS"),
        (closed_e2e, "LIVE_E2E_MISSING"),
    ]:
        if ok is not True:
            reasons.append(r)
    return LiveEvidence(not reasons, tuple(reasons))
