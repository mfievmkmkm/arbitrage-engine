"""Read-only market and historical-rate evidence for virtual funding positions."""

import asyncio, math, time
from dataclasses import dataclass
from .contract_book import to_base_levels
from .spot_future_vwap import vwap
from .instruments import from_market, compatible, min_notional_ok


@dataclass(frozen=True)
class History:
    verified: bool
    amount: float
    events: tuple
    reason: str
    covered_until: float = 0


class Source:
    def __init__(
        self,
        clients,
        funding_service,
        notional,
        fees_pct=0.2,
        safety_pct=0.1,
        max_age=1.5,
        clock=time.time,
    ):
        if (
            not all(
                math.isfinite(float(x))
                for x in (notional, fees_pct, safety_pct, max_age)
            )
            or notional <= 0
            or min(fees_pct, safety_pct) < 0
            or max_age <= 0
        ):
            raise ValueError("FUNDING_SOURCE_CONFIG_INVALID")
        self.clients = clients
        self.fs = funding_service
        self.notional = notional
        self.fees = fees_pct
        self.safety = safety_pct
        self.max_age = max_age
        self.clock = clock

    async def quote(self, symbol, long, short, qty=None):
        try:
            if long == short:
                raise ValueError("FUNDING_VENUE_PAIR_INVALID")
            snapshots = await asyncio.gather(
                self.fs.get(long, symbol), self.fs.get(short, symbol)
            )
            rates = [float(x.rate) for x in snapshots]
            intervals = [float(x.interval_hours) for x in snapshots]
            nexts = [float(x.next_ts) / 1000 for x in snapshots]
            if (
                not all(math.isfinite(x) for x in (*rates, *intervals, *nexts))
                or min(intervals) <= 0
                or max(intervals) > 24
                or any(abs(x) >= 1 for x in rates)
            ):
                raise ValueError("FUNDING_FORECAST_INVALID")
            specs = []
            for venue in (long, short):
                market = self.clients[venue].market(symbol)
                size = float(market["contractSize"])
                if not math.isfinite(size) or size <= 0:
                    raise ValueError("CONTRACT_SIZE_INVALID")
                specs.append(from_market(venue, market))
            if not compatible(*specs):
                raise ValueError("INSTRUMENT_MISMATCH")

            async def book(venue, size):
                start = self.clock()
                b = await asyncio.wait_for(
                    self.clients[venue].fetch_order_book(symbol, limit=20), 8
                )
                stamp = (
                    float(b["timestamp"]) / 1000
                    if b.get("timestamp") is not None
                    else start
                )
                if (
                    not math.isfinite(stamp)
                    or not 0 <= self.clock() - stamp <= self.max_age
                ):
                    raise ValueError("FUNDING_BOOK_STALE")
                return (
                    to_base_levels(b["bids"], size),
                    to_base_levels(b["asks"], size),
                    stamp,
                )

            a, b = await asyncio.gather(
                *(book(v, s.contract_size) for v, s in zip((long, short), specs))
            )
            qty = self.notional / a[1][0][0] if qty is None else qty
            prices = (
                vwap(a[1], qty),
                vwap(b[0], qty),
                vwap(a[0], qty),
                vwap(b[1], qty),
            )
            if (
                any(x is None or not math.isfinite(x) or x <= 0 for x in prices)
                or not math.isfinite(qty)
                or qty <= 0
            ):
                raise ValueError("FUNDING_EXIT_DEPTH_LOW")
            if self.clock() - min(a[2], b[2]) > self.max_age:
                raise ValueError("FUNDING_BOOK_STALE")
            if any(
                not min_notional_ok(s, qty * price, price)
                for s, price in zip(specs, prices[:2])
            ):
                raise ValueError("FUNDING_ORDER_MINIMUM")
            return dict(
                ok=True,
                symbol=symbol,
                buy=long,
                sell=short,
                base_qty=qty,
                entry_buy=prices[0],
                entry_sell=prices[1],
                exit_buy=prices[2],
                exit_sell=prices[3],
                ts=min(a[2], b[2]),
                fee_pct=self.fees,
                safety_pct=self.safety,
                long_rate=rates[0],
                short_rate=rates[1],
                long_interval=intervals[0],
                short_interval=intervals[1],
                long_next=nexts[0],
                short_next=nexts[1],
                mode="PAPER_MODEL",
            )
        except Exception as error:
            return {
                "ok": False,
                "reason": (
                    str(error)
                    if isinstance(error, ValueError)
                    else "FUNDING_MARKET_UNAVAILABLE"
                ),
            }

    async def settlements(self, p, until):
        events = []
        verified = True
        reason = "VERIFIED_MODEL_HISTORY"
        due = []
        if not math.isfinite(until) or until < p["opened_at"]:
            return History(False, 0, (), "FUNDING_WINDOW_INVALID")
        for venue, side in ((p["buy"], -1), (p["sell"], 1)):
            prefix = "long" if side == -1 else "short"
            first = p[prefix + "_next"]
            period = p[prefix + "_interval"] * 3600
            if (
                not math.isfinite(first)
                or not math.isfinite(period)
                or period <= 0
                or first <= p["opened_at"]
            ):
                return History(False, 0, (), "FUNDING_CALENDAR_INVALID")
            expected = []
            stamp = first
            while stamp <= until:
                if stamp > p["opened_at"]:
                    expected.append(stamp)
                stamp += period
                if len(expected) > 100:
                    return History(False, 0, (), "FUNDING_HISTORY_WINDOW_TOO_LARGE")
            due.extend(expected)
            client = self.clients[venue]
            try:
                if client.has.get("fetchFundingRateHistory") is not True:
                    raise ValueError("FUNDING_PUBLIC_HISTORY_UNSUPPORTED")
                rows = await asyncio.wait_for(
                    client.fetch_funding_rate_history(
                        p["symbol"], int(p["opened_at"] * 1000), 100
                    ),
                    8,
                )
                if not isinstance(rows, list) or len(rows) >= 100:
                    raise ValueError("FUNDING_HISTORY_TRUNCATED")
                actual = {}
                for row in rows:
                    if row.get("symbol") != p["symbol"]:
                        continue
                    ts = float(row["timestamp"]) / 1000
                    rate = float(row["fundingRate"])
                    if (
                        not math.isfinite(ts)
                        or not math.isfinite(rate)
                        or abs(rate) >= 1
                    ):
                        raise ValueError("FUNDING_HISTORY_INVALID")
                    if not p["opened_at"] < ts <= until:
                        continue
                    matches = [x for x in expected if abs(ts - x) <= 1]
                    if len(matches) != 1:
                        raise ValueError("FUNDING_CALENDAR_CHANGED")
                    ts = matches[0]
                    if ts in actual and actual[ts] != rate:
                        raise ValueError("FUNDING_HISTORY_CONFLICT")
                    actual[ts] = rate
                if any(not any(abs(ts - x) <= 1 for ts in actual) for x in expected):
                    raise ValueError("FUNDING_SETTLEMENT_MISSING")
                entry = p["entry_buy"] if side == -1 else p["entry_sell"]
                for ts, rate in actual.items():
                    events.append(
                        dict(
                            venue=venue,
                            ts=ts,
                            rate=rate,
                            amount=side * rate * p["base_qty"] * entry,
                            mode="PUBLIC_HISTORY_ENTRY_REFERENCE_MODEL",
                        )
                    )
            except Exception as error:
                verified = False
                reason = (
                    str(error)
                    if isinstance(error, ValueError)
                    else "FUNDING_HISTORY_UNAVAILABLE"
                )
        cutoff = self.clock() - 30
        if until > self.clock() or any(ts > cutoff for ts in due):
            verified = False
            reason = "FUNDING_HISTORY_MATURITY_PENDING"
        return History(
            verified,
            sum(x["amount"] for x in events),
            tuple(events),
            reason,
            until if verified else min(until, cutoff),
        )
