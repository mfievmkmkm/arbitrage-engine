import asyncio, time, math
from .funding import FundingSnapshot
from .funding_cache import FundingCache
from .funding_timing import window, carry_pct
from .funding_interval import infer as infer_interval


class FundingService:
    def __init__(self, clients, ttl=120, timeout=6):
        self.clients = clients
        self.cache = FundingCache(ttl)
        self.timeout = timeout
        self.errors = {}

    async def get(self, exchange, symbol):
        cached = self.cache.get(exchange, symbol)
        if cached:
            return FundingSnapshot(
                exchange,
                symbol,
                cached["rate"],
                cached["next_ts"],
                cached.get("interval_hours"),
            )
        client = self.clients.get(exchange)
        if not client or not client.has.get("fetchFundingRate"):
            return FundingSnapshot(exchange, symbol, None, None, None)
        try:
            row = await asyncio.wait_for(
                client.fetch_funding_rate(symbol), self.timeout
            )
            raw_rate = row.get("fundingRate")
            rate = float(raw_rate) if raw_rate is not None else None
            if rate is not None and (not math.isfinite(rate) or abs(rate) >= 1):
                raise ValueError("INVALID_FUNDING_RATE")
            next_ts = row.get("fundingTimestamp") or row.get("nextFundingTimestamp")
            if next_ts is not None:
                next_ts = int(next_ts)
                if next_ts <= 0:
                    raise ValueError("INVALID_FUNDING_TIMESTAMP")
            interval = infer_interval(row)
            self.cache.put(
                exchange,
                symbol,
                rate,
                next_ts,
                interval.hours if interval.known else None,
            )
            return FundingSnapshot(
                exchange,
                symbol,
                rate,
                next_ts,
                interval.hours if interval.known else None,
            )
        except Exception as e:
            self.errors[(exchange, symbol)] = type(e).__name__
            return FundingSnapshot(exchange, symbol, None, None, None)

    async def pair_carry_pct(
        self, long_exchange, short_exchange, symbol, hold_seconds=1200
    ):
        a, b = await asyncio.gather(
            self.get(long_exchange, symbol), self.get(short_exchange, symbol)
        )
        if any(
            x.rate is None or x.next_ts is None or x.interval_hours is None
            for x in (a, b)
        ):
            return 0.0, False, "UNKNOWN"
        wa = window(a.next_ts, hold_seconds, a.interval_hours)
        wb = window(b.next_ts, hold_seconds, b.interval_hours)
        if any(w.reason in ("INVALID", "STALE", "UNKNOWN") for w in (wa, wb)):
            return 0.0, False, "CALENDAR_UNVERIFIED"
        if not all(math.isfinite(float(x.rate)) for x in (a, b)):
            return 0.0, False, "INVALID_RATE"
        return (
            (b.rate * wb.periods - a.rate * wa.periods) * 100,
            True,
            "PER_LEG_CALENDAR",
        )
