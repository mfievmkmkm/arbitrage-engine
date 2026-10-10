"""Read-only spot inventory. Missing balance fields never prove zero."""

import asyncio
import time
from dataclasses import dataclass
from .native_order_plan import number


@dataclass(frozen=True)
class Snapshot:
    venue: str
    balances: dict
    orders: tuple
    started_at: float
    received_at: float

    def asset(self, currency):
        if currency not in self.balances:
            raise ValueError("SPOT_BALANCE_UNKNOWN")
        return self.balances[currency]

    def fresh(self, now):
        import math

        if (
            any(
                isinstance(x, bool) or not math.isfinite(x)
                for x in (now, self.started_at, self.received_at)
            )
            or not self.started_at <= self.received_at <= now
            or now - self.started_at > 15
        ):
            raise ValueError("SPOT_SNAPSHOT_STALE")
        return self


class Reader:
    def __init__(self, venue, client, timeout=8, clock=time.time):
        self.venue, self.client, self.timeout, self.clock = (
            venue,
            client,
            timeout,
            clock,
        )

    async def snapshot(self, currencies):
        started = self.clock()
        balance, orders = await asyncio.gather(
            asyncio.wait_for(self.client.fetch_balance(), self.timeout),
            asyncio.wait_for(self.client.fetch_open_orders(), self.timeout),
        )
        if not isinstance(balance, dict) or not isinstance(orders, list):
            raise ValueError("SPOT_ACCOUNT_RESPONSE_INVALID")
        out = {}
        for currency in set(currencies) | {"USDT"}:
            row = balance.get(currency)
            if not isinstance(row, dict):
                raise ValueError("SPOT_BALANCE_UNKNOWN")
            values = {
                k: number(row.get(k), "SPOT_" + k.upper(), positive=False)
                for k in ("free", "used", "total")
            }
            if abs(values["free"] + values["used"] - values["total"]) > max(
                values["total"] * number("1e-8", "TOLERANCE"),
                number("1e-12", "TOLERANCE"),
            ):
                raise ValueError("SPOT_BALANCE_CONFLICT")
            out[currency] = {k: float(v) for k, v in values.items()}
        for order in orders:
            if (
                not isinstance(order, dict)
                or not order.get("id")
                or not order.get("symbol")
            ):
                raise ValueError("SPOT_WORKING_ORDER_UNKNOWN")
            if order.get("side") not in ("buy", "sell"):
                raise ValueError("SPOT_WORKING_ORDER_UNKNOWN")
            amount = number(order.get("amount"), "SPOT_ORDER_AMOUNT")
            filled = number(order.get("filled"), "SPOT_ORDER_FILLED", positive=False)
            if filled > amount:
                raise ValueError("SPOT_ORDER_OVERFILL")
        return Snapshot(self.venue, out, tuple(orders), started, self.clock()).fresh(
            self.clock()
        )
