from dataclasses import dataclass
from .exchange_executor import SubmitRequest
from .execution_fees import resolve


@dataclass(frozen=True)
class EntryCostEstimate:
    long_fee: float
    short_fee: float
    total_fee: float
    net_edge_usd: float
    long_liquidity: str
    short_liquidity: str
    exit_fee: float = 0.0


def estimate(plan, policy, long_price, short_price, schedule):
    lr = SubmitRequest(
        plan.long.symbol,
        plan.long.side,
        plan.long.contracts,
        policy.order_type,
        long_price,
        False,
        policy.ioc,
    )
    sr = SubmitRequest(
        plan.short.symbol,
        plan.short.side,
        plan.short.contracts,
        policy.order_type,
        short_price,
        False,
        policy.ioc,
    )
    fees = resolve(plan.long.venue, plan.short.venue, lr, sr, schedule)
    lf = abs(long_price) * plan.long.base_amount * fees.long_rate
    sf = abs(short_price) * plan.short.base_amount * fees.short_rate
    gross = max(0, (short_price - long_price) * plan.base_amount)
    exit_fee = (
        max(abs(long_price), abs(short_price))
        * plan.base_amount
        * (
            schedule.require(plan.long.venue, "taker")
            + schedule.require(plan.short.venue, "taker")
        )
    )
    return EntryCostEstimate(
        lf,
        sf,
        lf + sf,
        gross - lf - sf - exit_fee,
        fees.long_liquidity,
        fees.short_liquidity,
        exit_fee,
    )
