"""Bounded private funding history with explicit signed-cashflow policies.
Policy follows native income/bill records rather than projected public rates.
Full pages, missing IDs/currencies and immature settlement windows are unverified.
"""

import asyncio, math, time
from dataclasses import dataclass


@dataclass(frozen=True)
class Evidence:
    verified: bool
    amount: float
    events: tuple
    reason: str
    covered_until: float = 0


# CCXT Bybit parse_income exposes execFee (expense), while these other adapters
# expose signed account balance change. Unsupported adapters remain unverified.
POLICY = {
    "binance": ("income", 1),
    "bingx": ("income", 1),
    "bitget": ("amount", 1),
    "bybit": ("execFee", -1),
    "okx": ("balChg", 1),
}


class Reader:
    def __init__(self, venue, client, timeout=8, maturity_seconds=30, clock=time.time):
        self.venue = venue
        self.client = client
        self.timeout = timeout
        self.maturity = maturity_seconds
        self.clock = clock

    async def collect(self, symbol, since, until):
        if (
            self.venue not in POLICY
            or self.client.has.get("fetchFundingHistory") is not True
        ):
            return Evidence(False, 0, (), "FUNDING_HISTORY_UNSUPPORTED")
        cutoff = self.clock() - self.maturity
        if since <= 0 or until < since:
            return Evidence(False, 0, (), "FUNDING_WINDOW_INVALID")
        try:
            rows = await asyncio.wait_for(
                self.client.fetch_funding_history(symbol, int(since * 1000), 100),
                self.timeout,
            )
            if not isinstance(rows, list) or len(rows) >= 100:
                return Evidence(False, 0, (), "FUNDING_HISTORY_TRUNCATED")
            field, sign = POLICY[self.venue]
            events = {}
            for row in rows:
                if row.get("symbol") != symbol:
                    continue
                if row.get("timestamp") is None:
                    raise ValueError("FUNDING_TIMESTAMP_MISSING")
                stamp = float(row["timestamp"]) / 1000
                if not math.isfinite(stamp):
                    raise ValueError("FUNDING_TIMESTAMP_INVALID")
                if stamp < since or stamp > min(until, cutoff):
                    continue
                if row.get("code") not in ("USDT", "USD") or row.get("id") is None:
                    raise ValueError("FUNDING_ID_OR_CURRENCY_UNKNOWN")
                raw = row.get("info") or {}
                native = raw.get(field)
                if self.venue == "okx" and (native is None or float(native) == 0):
                    native = raw.get("posBalChg")
                if native is None:
                    raise ValueError("FUNDING_CASHFLOW_SIGN_UNVERIFIED")
                amount = float(native) * sign
                if not math.isfinite(amount):
                    raise ValueError("FUNDING_AMOUNT_INVALID")
                event = {
                    "venue": self.venue,
                    "event_id": str(row["id"]),
                    "symbol": symbol,
                    "ts": stamp,
                    "amount": amount,
                    "source": "native_private_income",
                }
                if event["event_id"] in events and events[event["event_id"]] != event:
                    raise ValueError("FUNDING_DUPLICATE_CONFLICT")
                events[event["event_id"]] = event
            events = tuple(events.values())
            total = sum(x["amount"] for x in events)
            mature = until <= cutoff
            return Evidence(
                mature,
                total,
                events,
                "VERIFIED" if mature else "FUNDING_SETTLEMENT_PENDING",
                min(until, cutoff),
            )
        except (Exception,):
            return Evidence(False, 0, (), "FUNDING_HISTORY_UNAVAILABLE")


class PairReader:
    def __init__(self, readers, calendar=None, clock=time.time):
        self.readers = readers
        self.calendar, self.clock = calendar, clock

    async def collect(self, trade, until):
        names = (trade.long_venue, trade.short_venue)
        if any(v not in self.readers for v in names):
            return Evidence(False, 0, (), "FUNDING_VENUE_MISSING")
        rows = await asyncio.gather(
            *(
                self.readers[v].collect(trade.symbol, trade.opened_at, until)
                for v in names
            )
        )
        return Evidence(
            all(x.verified for x in rows),
            sum(x.amount for x in rows),
            tuple(e for x in rows for e in x.events),
            ";".join(x.reason for x in rows),
            min(x.covered_until for x in rows),
        )

    async def mark(self, trade, until):
        """Mature income plus public calendar proof of no newer settlement."""
        if self.calendar is None:
            return await self.collect(trade, until)
        names = (trade.long_venue, trade.short_venue)
        if any(v not in self.readers for v in names):
            return Evidence(False, 0, (), "FUNDING_VENUE_MISSING")
        cutoff = until - max(self.readers[v].maturity for v in names)
        if cutoff < trade.opened_at:
            return Evidence(False, 0, (), "FUNDING_ENTRY_MATURITY_PENDING")
        mature = await self.collect(trade, cutoff)
        if not mature.verified:
            return mature
        try:
            snaps = await asyncio.gather(
                *(self.calendar.get(v, trade.symbol) for v in names)
            )
            from .native_order_plan import number

            for v, s in zip(names, snaps):
                if (s.exchange, s.symbol) != (v, trade.symbol):
                    raise ValueError("FUNDING_CALENDAR_SCOPE_MISMATCH")
                nxt = float(number(s.next_ts, "FUNDING_TIMESTAMP"))
                nxt = nxt / 1000 if nxt > 10_000_000_000 else nxt
                interval = float(number(s.interval_hours, "FUNDING_INTERVAL")) * 3600
                if interval > 86400 or nxt <= self.clock() or nxt - interval > cutoff:
                    raise ValueError("FUNDING_RECENT_SETTLEMENT_PENDING")
            return Evidence(
                True,
                mature.amount,
                mature.events,
                "MATURE_PRIVATE_INCOME_CALENDAR",
                until,
            )
        except Exception:
            return Evidence(
                False, mature.amount, mature.events, "FUNDING_CALENDAR_GAP", cutoff
            )
