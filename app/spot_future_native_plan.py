"""Spot-first sizing: reserve base fees before planning a derivative hedge.

This module grants no authority. A hedge is recomputed after terminal spot
fills, using the asset actually credited, never the submitted gross amount.
"""

from dataclasses import dataclass, asdict
from decimal import localcontext
from .native_order_plan import (
    number,
    format_value,
    market as future_market,
    validate_request as future_validate,
)
from .spot_native_order import market as spot_market, validate_request as spot_validate
from .exchange_executor import SubmitRequest


def rate(value):
    x = number(value, "SPOT_FEE_RATE", positive=False)
    if x > number("0.1", "FEE_LIMIT"):
        raise ValueError("SPOT_FEE_RATE_INVALID")
    return x


def down(client, symbol, value):
    rounded = format_value(client, symbol, value, "amount")
    if rounded > value:
        raise ValueError("CASH_ROUNDING_INCREASES_EXPOSURE")
    return rounded


@dataclass(frozen=True)
class Plan:
    valid: bool
    reason: str
    spot: SubmitRequest | None = None
    future: SubmitRequest | None = None
    expected_spot_credit: float = 0
    hedged_base: float = 0
    unhedged_base: float = 0
    evidence: str = "PUBLIC_NATIVE_PLAN_NOT_ACCOUNT_ACCEPTANCE"
    release_authorized: bool = False

    def row(self):
        return asdict(self)


def hedge(client, symbol, credited_base, price, reduce_only=False):
    m = future_market(client, symbol)
    size = number(m["contractSize"], "CONTRACT_SIZE")
    qty = down(client, symbol, number(credited_base, "CREDITED_BASE") / size)
    p = format_value(client, symbol, number(price, "FUTURE_PRICE"), "price")
    r = SubmitRequest(
        symbol,
        "buy" if reduce_only else "sell",
        float(qty),
        "limit",
        float(p),
        reduce_only,
        True,
    )
    future_validate(client, r)
    return r, float(qty * size)


def prepare(
    spot_client,
    future_client,
    spot_symbol,
    future_symbol,
    requested_base,
    spot_price,
    future_price,
    spot_fee_rate,
    max_dust_usd=0.05,
):
    try:
        with localcontext() as ctx:
            ctx.prec = 50
            sm, fm = spot_market(spot_client, spot_symbol), future_market(
                future_client, future_symbol
            )
            if sm["base"] != fm["base"] or sm["quote"] != fm["quote"]:
                raise ValueError("CASH_INSTRUMENT_MISMATCH")
            gross = down(spot_client, spot_symbol, number(requested_base, "SPOT_BASE"))
            p = format_value(
                spot_client, spot_symbol, number(spot_price, "SPOT_PRICE"), "price"
            )
            spot = SubmitRequest(
                spot_symbol, "buy", float(gross), "limit", float(p), False, True
            )
            spot_validate(spot_client, spot)
            credited = gross * (1 - rate(spot_fee_rate))
            future, base = hedge(future_client, future_symbol, credited, future_price)
            residual = credited - number(base, "HEDGED_BASE")
            cap = number(max_dust_usd, "DUST_CAP", positive=False)
            if residual * p > cap:
                raise ValueError("CASH_UNHEDGED_RESIDUAL_LIMIT")
            # Both normal close legs must be representable before the purchase.
            spot_close(spot_client, spot_symbol, credited, p, spot_fee_rate)
            hedge(future_client, future_symbol, base, future_price, reduce_only=True)
            return Plan(
                True, "OK", spot, future, float(credited), base, float(residual)
            )
    except (ValueError, TypeError, KeyError, ArithmeticError) as error:
        return Plan(False, str(error))


def spot_close(client, symbol, available_owned_base, price, sell_base_fee_rate):
    # Some venues debit the sale fee in base as well. Never sell old inventory.
    market = spot_market(client, symbol)
    qty = down(
        client,
        symbol,
        number(available_owned_base, "OWNED_BASE") / (1 + rate(sell_base_fee_rate)),
    )
    p = format_value(client, symbol, number(price, "SPOT_EXIT_PRICE"), "price")
    r = SubmitRequest(symbol, "sell", float(qty), "limit", float(p), False, True)
    spot_validate(client, r)
    return r
