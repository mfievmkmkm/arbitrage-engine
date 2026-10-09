from dataclasses import dataclass
import math


@dataclass(frozen=True)
class CloseRecovery:
    required: bool
    venue: str | None
    side: str | None
    contracts: float
    reason: str


def plan(trade, exit_result, tolerance=1e-12):
    values = (
        trade.long_contracts,
        trade.short_contracts,
        exit_result.long_result.filled,
        exit_result.short_result.filled,
    )
    try:
        if any(
            isinstance(v, bool) or not math.isfinite(float(v)) or v < 0 for v in values
        ):
            raise ValueError()
        if (
            exit_result.long_result.filled > trade.long_contracts + tolerance
            or exit_result.short_result.filled > trade.short_contracts + tolerance
        ):
            raise ValueError()
    except (TypeError, ValueError):
        return CloseRecovery(True, None, None, 0, "INVALID_CLOSE_FILL_EVIDENCE")
    tolerance = min(tolerance, min(trade.long_contracts, trade.short_contracts) * 1e-10)
    lr = max(0, trade.long_contracts - exit_result.long_result.filled)
    sr = max(0, trade.short_contracts - exit_result.short_result.filled)
    if lr <= tolerance and sr <= tolerance:
        return CloseRecovery(False, None, None, 0, "FLAT_BY_FILLS")
    if lr > tolerance and sr <= tolerance:
        return CloseRecovery(True, trade.long_venue, "sell", lr, "LONG_RESIDUAL")
    if sr > tolerance and lr <= tolerance:
        return CloseRecovery(True, trade.short_venue, "buy", sr, "SHORT_RESIDUAL")
    return CloseRecovery(True, None, None, 0, "BOTH_LEGS_RESIDUAL_RECONCILE")
