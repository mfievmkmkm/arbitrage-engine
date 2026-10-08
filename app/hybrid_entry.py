"""Optional market fallback only after two terminal zero-fill IOC orders."""

import math, time
from dataclasses import dataclass
from .native_order_plan import PairPlan
from .order_settlement import valid, terminal
from .order_policy import OrderPolicy


@dataclass(frozen=True)
class FallbackQuote:
    native: PairPlan
    long_price: float
    short_price: float
    book_ts: float
    books_verified: bool = False


def assess(
    long_result,
    short_result,
    plan,
    quote,
    long_reference,
    short_reference,
    now=None,
    max_age=1.5,
    max_slippage_pct=0.2,
):
    now = time.time() if now is None else now
    if not valid(long_result, plan.long.contracts) or not valid(
        short_result, plan.short.contracts
    ):
        return False, "IOC_EVIDENCE_INVALID"
    if not terminal(long_result, plan.long.contracts) or not terminal(
        short_result, plan.short.contracts
    ):
        return False, "IOC_NOT_TERMINAL"
    if long_result.fee != 0 or short_result.fee != 0:
        return False, "ZERO_FILL_FEE_ACCOUNTING_REQUIRED"
    if long_result.filled != 0 or short_result.filled != 0:
        return False, "IOC_ALREADY_HAS_EXPOSURE"
    if not isinstance(quote, FallbackQuote) or quote.books_verified is not True:
        return False, "FRESH_BOOK_PROOF_REQUIRED"
    values = (
        now,
        quote.book_ts,
        quote.long_price,
        quote.short_price,
        long_reference,
        short_reference,
        max_age,
        max_slippage_pct,
    )
    if (
        not all(math.isfinite(float(x)) for x in values)
        or min(
            quote.long_price,
            quote.short_price,
            long_reference,
            short_reference,
            max_age,
        )
        <= 0
        or max_slippage_pct < 0
    ):
        return False, "FALLBACK_QUOTE_INVALID"
    if not 0 <= now - quote.book_ts <= max_age:
        return False, "FALLBACK_BOOK_STALE"
    n = quote.native
    if n.valid is not True or n.long is None or n.short is None:
        return False, "FALLBACK_NATIVE_PLAN_INVALID"
    if (
        n.long.symbol != plan.long.symbol
        or n.short.symbol != plan.short.symbol
        or n.long.side != "buy"
        or n.short.side != "sell"
        or not math.isclose(n.long.qty, plan.long.contracts, rel_tol=1e-10)
        or not math.isclose(n.short.qty, plan.short.contracts, rel_tol=1e-10)
        or not math.isclose(n.base_qty, plan.base_amount, rel_tol=1e-10)
    ):
        return False, "FALLBACK_PLAN_CHANGED"
    cap = max_slippage_pct / 100
    if quote.long_price > long_reference * (
        1 + cap
    ) or quote.short_price < short_reference * (1 - cap):
        return False, "FALLBACK_SLIPPAGE_CAP"
    return True, "OK"


def policy(max_slippage_pct=0.2):
    return OrderPolicy(
        "market", False, max_slippage_pct, "VERIFIED_ZERO_FILL_IOC_FALLBACK"
    )
