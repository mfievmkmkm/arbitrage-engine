"""Rebuild forward cash-and-carry ownership from terminal durable intents.

No mark-to-market profit or false-flat claim for unsold spot inventory.
Unknown orders or unknown fee currency make the entire reconstruction invalid.
"""

import math
from dataclasses import dataclass, asdict
from decimal import Decimal, localcontext
from .native_order_plan import number

TERMINAL = {"FILLED", "CANCELED", "CANCELLED", "REJECTED", "FAILED"}


@dataclass(frozen=True)
class Cashflow:
    spot_base: float
    future_base: float
    spot_cash: float
    future_realized: float
    quote_fees: float
    base_fees: float
    spot_cost_basis: float
    base_fee_usd: float = 0
    future_entry_price: float | None = None

    def row(self):
        return asdict(self)

    def net(self, funding, funding_verified):
        if funding_verified is not True or self.future_base != 0:
            raise ValueError("CASH_FINAL_ACCOUNTING_UNVERIFIED")
        if isinstance(funding, bool) or not math.isfinite(float(funding)):
            raise ValueError("CASH_FUNDING_INVALID")
        # All cash spent stays charged even when a small asset remainder is held.
        return self.spot_cash + self.future_realized - self.quote_fees + float(funding)


def rebuild(
    intents,
    trade_id,
    spot_venue,
    future_venue,
    spot_symbol,
    future_symbol,
    base,
    contract_size,
):
    if not isinstance(intents, dict) or not intents:
        raise ValueError("CASH_INTENTS_MISSING")
    if (
        not trade_id
        or not base
        or (spot_venue, spot_symbol) == (future_venue, future_symbol)
    ):
        raise ValueError("CASH_SCOPE_INVALID")
    with localcontext() as ctx:
        ctx.prec = 50
        size = number(contract_size, "CONTRACT_SIZE")
        spot_qty = future_qty = spot_cash = realized = fees = base_fees = cost = (
            future_cost
        ) = base_fee_usd = Decimal(0)
        seen_orders = set()
        # SQLite rowid survives updates/restart; timestamps cannot reorder stages.
        rows = list(intents.items())
        if any(not isinstance(r, dict) for _, r in rows):
            raise ValueError("CASH_INTENT_INVALID")
        sequences = [r.get("_journal_sequence") for _, r in rows]
        if any(type(s) is not int or s <= 0 for s in sequences) or len(
            set(sequences)
        ) != len(rows):
            raise ValueError("CASH_JOURNAL_SEQUENCE_UNKNOWN")
        rows.sort(key=lambda pair: pair[1]["_journal_sequence"])
        for iid, r in rows:
            if r.get("intent_id") != iid or r.get("trade_id") != trade_id:
                raise ValueError("CASH_INTENT_IDENTITY_MISMATCH")
            if r.get("state") not in TERMINAL:
                raise ValueError("CASH_ORDER_UNRESOLVED")
            scope = r.get("venue"), r.get("symbol")
            if scope not in ((spot_venue, spot_symbol), (future_venue, future_symbol)):
                raise ValueError("CASH_ORDER_SCOPE_MISMATCH")
            requested = number(r.get("qty"), "ORDER_QTY")
            q = number(r.get("filled"), "FILLED", positive=False)
            if q > requested:
                raise ValueError("CASH_OVERFILL")
            fee = r.get("fee")
            if isinstance(fee, bool) or fee is None or not math.isfinite(float(fee)):
                raise ValueError("CASH_FEE_UNKNOWN")
            fee = Decimal(str(fee))
            if q == 0:
                if fee != 0 or r.get("base_fee", 0) != 0:
                    raise ValueError("CASH_ZERO_FILL_COST_CONFLICT")
                continue
            oid = r.get("order_id")
            if not oid or (scope, oid) in seen_orders:
                raise ValueError("CASH_DUPLICATE_ORDER")
            seen_orders.add((scope, oid))
            price = number(r.get("avg_price"), "FILL_PRICE")
            if (
                r.get("side") not in ("buy", "sell")
                or type(r.get("reduce_only")) not in (bool, int)
                or r.get("reduce_only") not in (0, 1)
            ):
                raise ValueError("CASH_ORDER_FLAGS_INVALID")
            fees += fee
            if scope == (spot_venue, spot_symbol):
                if r.get("reduce_only") or r.get("base_currency") != base:
                    raise ValueError("CASH_SPOT_FEE_UNITS_UNKNOWN")
                raw = r.get("base_fee")
                if (
                    isinstance(raw, bool)
                    or raw is None
                    or not math.isfinite(float(raw))
                ):
                    raise ValueError("CASH_SPOT_FEE_UNKNOWN")
                bf = Decimal(str(raw))
                if abs(bf) > q:
                    raise ValueError("CASH_SPOT_FEE_INVALID")
                base_fees += bf
                base_fee_usd += bf * price
                if r["side"] == "buy":
                    credit = q - bf
                    if credit <= 0:
                        raise ValueError("CASH_ZERO_SPOT_CREDIT")
                    spot_qty += credit
                    cost += q * price
                    spot_cash -= q * price
                else:
                    debit = q + bf
                    if debit <= 0 or debit > spot_qty:
                        raise ValueError("CASH_SELL_EXCEEDS_OWNED_INVENTORY")
                    cost *= (spot_qty - debit) / spot_qty
                    spot_qty -= debit
                    spot_cash += q * price
            else:
                if r.get("base_fee", 0) != 0 or r.get("base_currency") is not None:
                    raise ValueError("CASH_FUTURE_FEE_UNITS_INVALID")
                q *= size
                if r["side"] == "sell" and not r["reduce_only"]:
                    if future_qty + q > spot_qty:
                        raise ValueError("CASH_HEDGE_EXCEEDS_SPOT_CREDIT")
                    future_qty += q
                    future_cost += q * price
                elif r["side"] == "buy" and r["reduce_only"]:
                    if q > future_qty:
                        raise ValueError("CASH_CLOSE_EXCEEDS_FUTURE")
                    entry = future_cost / future_qty
                    realized += q * (entry - price)
                    future_cost -= q * entry
                    future_qty -= q
                else:
                    raise ValueError("CASH_FUTURE_DIRECTION_INVALID")
        flow = Cashflow(
            *map(
                float,
                (
                    spot_qty,
                    future_qty,
                    spot_cash,
                    realized,
                    fees,
                    base_fees,
                    cost,
                    base_fee_usd,
                ),
            )
        )
        from dataclasses import replace

        return replace(
            flow,
            future_entry_price=(
                float(future_cost / future_qty) if future_qty > 0 else None
            ),
        )


def verify_private(
    flow,
    baseline_total,
    spot_snapshot,
    base,
    future_positions,
    future_symbol,
    contract_size,
    now,
):
    spot_snapshot.fresh(now)
    if spot_snapshot.orders:
        raise ValueError("CASH_WORKING_SPOT_ORDER")
    before = number(baseline_total, "BASELINE_BASE", positive=False)
    actual = number(
        spot_snapshot.asset(base)["total"], "PRIVATE_SPOT_BASE", positive=False
    )
    owned = number(flow.spot_base, "OWNED_SPOT_BASE", positive=False)
    expected = before + owned
    # Only allow float representation error of a large pre-existing balance.
    # A fixed dust threshold must never hide a missing small owned position.
    representation = Decimal(str(math.ulp(float(expected)))) * 2
    if owned > 0 and owned <= representation * 10:
        raise ValueError("CASH_PRIVATE_BALANCE_RESOLUTION_INSUFFICIENT")
    tolerance = max(owned * Decimal("1e-10"), representation)
    if abs(actual - expected) > tolerance:
        raise ValueError("CASH_PRIVATE_SPOT_MISMATCH")
    free = number(
        spot_snapshot.asset(base).get("free"), "SPOT_FREE_BASE", positive=False
    )
    if free + representation < owned:
        raise ValueError("CASH_OWNED_INVENTORY_NOT_FREE")
    if not isinstance(future_positions, list):
        raise ValueError("CASH_PRIVATE_FUTURE_UNKNOWN")
    size = number(contract_size, "CONTRACT_SIZE")
    q = Decimal(0)
    for p in future_positions:
        if (
            getattr(p, "symbol", None) != future_symbol
            or getattr(p, "side", None) != "short"
        ):
            raise ValueError("CASH_UNMANAGED_FUTURE_POSITION")
        contracts = number(
            getattr(p, "contracts", None), "PRIVATE_CONTRACTS", positive=False
        )
        ps = number(getattr(p, "contract_size", None), "PRIVATE_CONTRACT_SIZE")
        if ps != size:
            raise ValueError("CASH_PRIVATE_CONTRACT_SIZE_MISMATCH")
        q += contracts * size
    expected = number(flow.future_base, "OWNED_FUTURE_BASE", positive=False)
    if abs(q - expected) > expected * Decimal("1e-10"):
        raise ValueError("CASH_PRIVATE_FUTURE_MISMATCH")
    return dict(
        spot_owned=flow.spot_base,
        future_owned=flow.future_base,
        future_flat=flow.future_base == 0,
        spot_flat=flow.spot_base == 0,
        held_inventory_cost=flow.spot_cost_basis,
    )
