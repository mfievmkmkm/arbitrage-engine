import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Economics:
    allowed: bool
    carry_pct: float
    net_pct: float
    reason: str


def calculate(
    rate_long_pct,
    rate_short_pct,
    holding_hours,
    long_interval,
    short_interval,
    roundtrip_fees_pct,
    basis_risk_pct,
    safety_pct,
):
    values = (
        rate_long_pct,
        rate_short_pct,
        holding_hours,
        long_interval,
        short_interval,
        roundtrip_fees_pct,
        basis_risk_pct,
        safety_pct,
    )
    try:
        values = tuple(float(x) for x in values)
        if (
            not all(math.isfinite(x) for x in values)
            or min(values[2:5]) <= 0
            or min(values[5:]) < 0
        ):
            raise ValueError()
    except (ValueError, TypeError):
        return Economics(False, 0, 0, "INPUT_UNVERIFIED")
    rl, rs, hold, li, si, fees, basis, safety = values
    carry = rs * hold / si - rl * hold / li
    net = carry - fees - basis - safety
    return Economics(net > 0, carry, net, "OK" if net > 0 else "NET_NONPOSITIVE")
