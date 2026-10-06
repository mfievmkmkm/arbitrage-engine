from app.entry_cost import estimate
from app.executor_plan import build
from app.order_policy import OrderPolicy
from app.fee_schedule import FeeSchedule, FeeRate


def test_entry_cost_uses_policy_and_venue_rates():
    p = build("X", "a", "b", 1, 1, 1, lambda x: x, lambda x: x)
    s = FeeSchedule({"a": FeeRate(0.001, 0.002), "b": FeeRate(0.003, 0.004)})
    x = estimate(p, OrderPolicy("limit", True, 0.1, "IOC"), 100, 110, s)
    assert x.long_liquidity == "taker" and x.short_liquidity == "taker"
    assert abs(x.total_fee - (0.2 + 0.44)) < 1e-12
    assert abs(x.net_edge_usd - 8.70) < 1e-12
