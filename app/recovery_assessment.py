"""Counterfactual recovery economics. Recommendations never authorize orders."""

import asyncio
import math
from dataclasses import dataclass, asdict
from .exchange_executor import SubmitRequest
from .native_order_plan import number
from .order_settlement import valid, terminal
from .recovery_market import prepare, validate_evidence, MAX_SLIPPAGE


@dataclass(frozen=True)
class Assessment:
    action: str
    reason: str
    flatten_request: object = None
    complete_request: object = None
    flatten_net: float | None = None
    complete_net_model: float | None = None
    incremental_complete_net_model: float | None = None
    model: dict | None = None
    release_authorized: bool = False

    def row(self):
        return asdict(self)


async def assess(
    plan,
    long_result,
    short_result,
    reader,
    schedule,
    fees_verified=False,
    risk_verified=False,
    funding_cost=None,
    capture_fraction=0.5,
    safety_usd=0.02,
    min_net_usd=0.1,
    min_improvement_usd=0.02,
    max_notional_usd=5,
    timeout=8,
):
    if reader is None or plan.valid is not True or plan.long.venue == plan.short.venue:
        return Assessment("BLOCKED", "RECOVERY_PLAN_INVALID")
    legs = (plan.long, plan.short)
    fills = (long_result, short_result)
    if any(
        not valid(r, l.contracts) or not terminal(r, l.contracts)
        for l, r in zip(legs, fills)
    ):
        return Assessment("BLOCKED", "RECOVERY_TERMINAL_FILLS_REQUIRED")
    try:
        for l in legs:
            number(l.contract_size, "CONTRACT_SIZE")
        if any(
            isinstance(r.fee, bool) or not math.isfinite(float(r.fee)) for r in fills
        ):
            raise ValueError()
    except (ValueError, TypeError):
        return Assessment("BLOCKED", "RECOVERY_ACCOUNTING_INVALID")
    lb, sb = [r.filled * l.contract_size for l, r in zip(legs, fills)]
    if lb == sb:
        return Assessment("HEDGED" if lb > 0 else "NO_FILL", "NO_ADDITIONAL_ORDER")
    excess = abs(lb - sb)
    big = 0 if lb > sb else 1
    small = 1 - big
    full = max(lb, sb)
    surplus = legs[big]
    missing = legs[small]
    flat_req = SubmitRequest(
        surplus.symbol,
        "sell" if big == 0 else "buy",
        excess / surplus.contract_size,
        "market",
        None,
        True,
    )
    try:
        flat_req = await prepare(
            reader, surplus.venue, flat_req, surplus.contract_size, timeout
        )
    except Exception as e:
        return Assessment("BLOCKED", "FLATTEN_QUOTE_UNVERIFIED:" + str(e))
    # Unknown fees still permit the read-only reduce-only recommendation, but no NET.
    flat_net = None
    try:
        rates = [schedule.require(l.venue, "taker") for l in legs]
        paid_surplus = fills[big].fee * excess / max(lb, sb)
        direction = 1 if big == 0 else -1
        flat_net = (
            direction * (flat_req.reference_price - fills[big].avg_price) * excess
            - paid_surplus
            - flat_req.reference_price * excess * rates[big]
        )
        capture = float(number(capture_fraction, "CAPTURE"))
        if capture > 1:
            raise ValueError("CAPTURE_INVALID")
        safety = float(number(safety_usd, "SAFETY", False))
        min_net = float(number(min_net_usd, "MIN_NET", False))
        improvement = float(number(min_improvement_usd, "IMPROVEMENT", False))
        cap = float(number(max_notional_usd, "NOTIONAL_CAP"))
        funding = float(number(funding_cost, "FUNDING_COST", False))
    except (ValueError, RuntimeError, TypeError) as e:
        return Assessment(
            "FLATTEN",
            "COMPLETE_ECONOMICS_UNKNOWN:" + str(e),
            flat_req,
            flatten_net=None,
        )
    if fees_verified is not True or risk_verified is not True:
        return Assessment(
            "FLATTEN",
            "COMPLETE_PRIVATE_EVIDENCE_REQUIRED",
            flat_req,
            flatten_net=flat_net if fees_verified is True else None,
        )
    complete_req = SubmitRequest(
        missing.symbol,
        missing.side,
        excess / missing.contract_size,
        "market",
        None,
        False,
    )
    try:
        complete_req, le, se = await asyncio.gather(
            prepare(
                reader, missing.venue, complete_req, missing.contract_size, timeout
            ),
            prepare(
                reader,
                legs[0].venue,
                SubmitRequest(
                    legs[0].symbol,
                    "sell",
                    full / legs[0].contract_size,
                    "market",
                    None,
                    True,
                ),
                legs[0].contract_size,
                timeout,
            ),
            prepare(
                reader,
                legs[1].venue,
                SubmitRequest(
                    legs[1].symbol,
                    "buy",
                    full / legs[1].contract_size,
                    "market",
                    None,
                    True,
                ),
                legs[1].contract_size,
                timeout,
            ),
        )
        now = reader.clock()
        for req, v in (
            (flat_req, surplus.venue),
            (complete_req, missing.venue),
            (le, legs[0].venue),
            (se, legs[1].venue),
        ):
            validate_evidence(req, v, now)
    except Exception as e:
        return Assessment(
            "FLATTEN",
            "COMPLETE_QUOTE_UNVERIFIED:" + str(e),
            flat_req,
            flatten_net=flat_net,
        )
    prices = [r.avg_price or 0 for r in fills]
    bases = [lb, sb]
    prices[small] = (
        prices[small] * bases[small] + complete_req.reference_price * excess
    ) / full
    gap = prices[1] - prices[0]
    exit_gap = max(0, gap) * (1 - capture)
    extra_fee = complete_req.reference_price * excess * rates[small]
    exit_fees = full * (le.reference_price * rates[0] + se.reference_price * rates[1])
    paid = sum(r.fee for r in fills)
    extra_slip = complete_req.reference_price * excess * MAX_SLIPPAGE
    exit_slip = full * (le.reference_price + se.reference_price) * MAX_SLIPPAGE
    total_cost = (
        paid + extra_fee + exit_fees + funding + safety + extra_slip + exit_slip
    )
    net = (gap - exit_gap) * full - total_cost
    marginal_gap = (
        (complete_req.reference_price - fills[big].avg_price)
        if big == 0
        else (fills[big].avg_price - complete_req.reference_price)
    )
    marginal = (
        (marginal_gap - exit_gap) * excess
        - paid_surplus
        - extra_fee
        - extra_slip
        - (exit_fees + funding + safety + exit_slip) * excess / full
    )
    notional = full * max(prices[0], prices[1], le.reference_price, se.reference_price)
    reasons = []
    if gap <= 0:
        reasons.append("ENTRY_GAP_NONPOSITIVE")
    if notional > cap:
        reasons.append("RECOVERY_NOTIONAL_CAP")
    if net < min_net:
        reasons.append("RECOVERY_FULL_NET_TOO_LOW")
    if marginal < flat_net + improvement:
        reasons.append("RECOVERY_INCREMENTAL_NET_TOO_LOW")
    model = dict(
        kind="CONVERGENCE_MODEL_NOT_REALIZED_PNL",
        base_qty=full,
        entry_gap=gap,
        target_exit_gap=exit_gap,
        capture_fraction=capture,
        paid_entry_fees=paid,
        extra_entry_fee=extra_fee,
        exit_fees=exit_fees,
        slippage_reserve_usd=extra_slip + exit_slip,
        break_even_capture_fraction=total_cost / (gap * full) if gap > 0 else None,
        immediate_close_net_quote=(
            le.reference_price - prices[0] + prices[1] - se.reference_price
        )
        * full
        - total_cost,
        funding_cost=funding,
        safety_usd=safety,
        notional_usd=notional,
        min_net_usd=min_net,
        min_improvement_usd=improvement,
        quotes_at=now,
        long_exit_request=asdict(le),
        short_exit_request=asdict(se),
    )
    return Assessment(
        "FLATTEN" if reasons else "COMPLETE",
        ",".join(reasons) if reasons else "MODEL_NET_AND_INCREMENTAL_ADVANTAGE",
        flat_req,
        complete_req,
        flat_net,
        net,
        marginal,
        model,
    )


def reduction_effects(plan, initial, recovery):
    """Record partial reduction cashflow without claiming private-flat or final PNL."""
    if recovery.action != "FLATTEN" or recovery.result is None:
        return None
    legs = (plan.long, plan.short)
    fills = (initial.long_result, initial.short_result)
    bases = [r.filled * l.contract_size for l, r in zip(legs, fills)]
    big = 0 if bases[0] > bases[1] else 1
    rr = recovery.result
    excess = abs(bases[0] - bases[1])
    target = excess / legs[big].contract_size
    if (
        not valid(rr, target)
        or isinstance(rr.fee, bool)
        or rr.fee is None
        or not math.isfinite(float(rr.fee))
    ):
        raise ValueError("REDUCTION_EVIDENCE_INVALID")
    reduced = rr.filled * legs[big].contract_size
    gross = (
        (rr.avg_price - fills[big].avg_price) * reduced * (1 if big == 0 else -1)
        if reduced
        else 0
    )
    allocated = fills[big].fee * reduced / bases[big]
    bases[big] = max(0, bases[big] - reduced)
    return dict(
        kind="REALIZED_REDUCTION_EXCLUDES_FUNDING",
        reduced_base=reduced,
        gross=gross,
        allocated_entry_fee=allocated,
        reduction_fee=rr.fee,
        net_excluding_funding=gross - allocated - rr.fee,
        remaining_long_base=bases[0],
        remaining_short_base=bases[1],
        private_verified=False,
        flat_by_fills=bases[0] == bases[1] == 0,
    )
