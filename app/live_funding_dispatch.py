"""Funding selection over the common durable derivative execution lifecycle.

Forecasts select direction but never subsidize a negative entry edge. This
conservative policy is deliberate for a $40–50 account: settlement rates can
change before payment. Private income alone enters realized/exit accounting.
"""

import asyncio
import math
from .live_entry_dispatch import Coordinator as DerivativeCoordinator
from .funding_timing import window
from .native_order_plan import number
from .private_funding_reader import POLICY


class Coordinator(DerivativeCoordinator):
    strategy = "funding_arb"

    def __init__(self, *args, min_carry_pct=0.03, **kwargs):
        super().__init__(*args, **kwargs)
        self.min_carry = float(
            number(min_carry_pct, "FUNDING_MIN_CARRY", positive=False)
        )
        if not 60 <= self.max_seconds <= 86400:
            raise ValueError("FUNDING_HOLD_CONFIG_INVALID")

    async def _prepare(self, op, require_authority=True):
        # Validate forecast before the parent fetches fresh IOC books.
        symbol, lv, sv = op["symbol"], op["buy"], op["sell"]
        if lv == sv or any(v not in POLICY for v in (lv, sv)):
            raise ValueError("FUNDING_PRIVATE_HISTORY_UNSUPPORTED")
        snapshots = await asyncio.wait_for(
            asyncio.gather(self.funding.get(lv, symbol), self.funding.get(sv, symbol)),
            8,
        )
        carry, loss_rate, calendars = 0.0, 0.0, []
        for venue, side, snap in zip((lv, sv), (-1, 1), snapshots):
            if (getattr(snap, "exchange", None), getattr(snap, "symbol", None)) != (
                venue,
                symbol,
            ):
                raise ValueError("FUNDING_SCOPE_MISMATCH")
            raw = getattr(snap, "rate", None)
            if isinstance(raw, bool) or raw is None:
                raise ValueError("FUNDING_RATE_UNKNOWN")
            rate = float(raw)
            if not math.isfinite(rate) or abs(rate) >= 1:
                raise ValueError("FUNDING_RATE_INVALID")
            interval = float(number(snap.interval_hours, "FUNDING_INTERVAL"))
            if interval > 24:
                raise ValueError("FUNDING_INTERVAL_INVALID")
            nxt = float(number(snap.next_ts, "FUNDING_TIMESTAMP"))
            nxt = nxt / 1000 if nxt > 10_000_000_000 else nxt
            w = window(nxt, self.max_seconds, interval, int(self.clock() * 1000))
            if w.reason in ("UNKNOWN", "INVALID", "STALE") or nxt - self.clock() < 60:
                raise ValueError("FUNDING_ENTRY_CALENDAR_UNTRUSTED")
            carry += side * rate * w.periods * 100
            loss_rate += max(0, -side * rate) * w.periods
            calendars.append(
                dict(
                    venue=venue,
                    rate=rate,
                    next_ts=nxt,
                    interval_hours=interval,
                    periods=w.periods,
                )
            )
        if not math.isfinite(carry) or carry <= self.min_carry:
            raise ValueError("FUNDING_CARRY_BELOW_THRESHOLD")
        if not any(x["periods"] for x in calendars):
            raise ValueError("FUNDING_NO_SETTLEMENT_IN_HORIZON")
        result = await super()._prepare(op, require_authority)
        # Do not let projected income offset a separately payable carry expense.
        plan, requests = result[:2]
        reserve = (
            max(
                (
                    r.qty * plan.long.contract_size * r.price
                    if v == lv
                    else r.qty * plan.short.contract_size * r.price
                )
                for v, r in requests.items()
            )
            * loss_rate
        )
        result = (*result[:3], result[3] + reserve, *result[4:])
        op["_funding_plan"] = dict(
            hold_seconds=self.max_seconds,
            forecast_carry_pct=carry,
            forecast_expense_reserve=reserve,
            calendars=calendars,
            policy="POSITIVE_ENTRY_NET_NO_FORECAST_INCOME_CREDIT",
        )
        return result

    def durable_metadata(self, op):
        return dict(strategy="funding_arb", funding_plan=op["_funding_plan"])

    async def process_rows(self, rows):
        offers = [
            dict(symbol=r["symbol"], buy=r["long_venue"], sell=r["short_venue"])
            for r in sorted(
                rows, key=lambda x: x.get("projected_net_pct", 0), reverse=True
            )[:5]
        ]
        return await self.process(offers)
