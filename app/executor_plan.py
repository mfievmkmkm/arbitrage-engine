import math
from dataclasses import dataclass


@dataclass(frozen=True)
class PlannedLeg:
    venue: str
    symbol: str
    side: str
    contracts: float
    contract_size: float
    base_amount: float


@dataclass(frozen=True)
class ExecutionPlan:
    long: PlannedLeg
    short: PlannedLeg
    base_amount: float
    valid: bool
    reason: str


def build(
    symbol,
    long_venue,
    short_venue,
    base_amount,
    long_contract_size,
    short_contract_size,
    long_round,
    short_round,
):
    try:
        raw = (base_amount, long_contract_size, short_contract_size)
        values = tuple(float(x) for x in raw)
        if (
            long_venue == short_venue
            or any(isinstance(x, bool) for x in raw)
            or not all(math.isfinite(x) and x > 0 for x in values)
        ):
            raise ValueError()
        base_amount, long_contract_size, short_contract_size = values
    except (ValueError, TypeError):
        return ExecutionPlan(None, None, 0, False, "INVALID_SIZE")
    try:
        lc = float(long_round(base_amount / long_contract_size))
        sc = float(short_round(base_amount / short_contract_size))
    except Exception:
        return ExecutionPlan(None, None, 0, False, "ROUNDING_UNVERIFIED")
    if not all(math.isfinite(x) and x > 0 for x in (lc, sc)):
        return ExecutionPlan(None, None, 0, False, "BELOW_MIN_SIZE")
    lb = lc * long_contract_size
    sb = sc * short_contract_size
    matched = min(lb, sb)
    scale = max(lb, sb)
    if not all(math.isfinite(x) for x in (lb, sb)) or scale > base_amount + max(
        base_amount * 1e-10, 1e-12
    ):
        return ExecutionPlan(None, None, 0, False, "ROUNDING_INCREASES_EXPOSURE")
    if matched <= 0:
        return ExecutionPlan(None, None, 0, False, "BELOW_MIN_SIZE")
    if abs(lb - sb) > scale * 0.001:
        return ExecutionPlan(None, None, matched, False, "EXPOSURE_MISMATCH")
    return ExecutionPlan(
        PlannedLeg(long_venue, symbol, "buy", lc, long_contract_size, lb),
        PlannedLeg(short_venue, symbol, "sell", sc, short_contract_size, sb),
        matched,
        True,
        "OK",
    )
