"""Read-only market and historical-rate evidence for virtual funding positions."""

import asyncio, math, time
from dataclasses import dataclass
from types import SimpleNamespace
from .contract_book import to_base_levels
from .spot_future_vwap import vwap
from .instruments import compatible, min_notional_ok
from .public_books import normalize
from .secondary_book_history import spec as history_spec


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
        history_store=None,
        book_history=None,
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
        self.history_store = history_store
        self.book_history = book_history
        self.book_recorded = self.book_record_failures = 0

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
                instrument = history_spec(venue, market)
                if instrument is None or instrument.contract is not True:
                    raise ValueError("CONTRACT_SIZE_INVALID")
                specs.append(instrument)
            if not compatible(*specs):
                raise ValueError("INSTRUMENT_MISMATCH")

            async def book(venue, size):
                start = self.clock()
                b = await asyncio.wait_for(
                    self.clients[venue].fetch_order_book(symbol, limit=20), 8
                )
                original_received = b.get("received_at", self.clock())
                b = normalize(
                    b,
                    symbol,
                    start,
                    self.clock(),
                    self.max_age,
                    b.get("data_source", "REST"),
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
                if (
                    isinstance(original_received, bool)
                    or not isinstance(original_received, (int, float))
                    or not math.isfinite(original_received)
                    or not stamp <= original_received <= self.clock()
                ):
                    raise ValueError("FUNDING_BOOK_RECEIPT_INVALID")
                bids, asks = to_base_levels(b["bids"], size), to_base_levels(
                    b["asks"], size
                )
                return (
                    bids,
                    asks,
                    stamp,
                    SimpleNamespace(
                        exchange=venue,
                        symbol=symbol,
                        fetched=stamp,
                        received_at=original_received,
                        data_source=b["data_source"],
                        bids=bids,
                        asks=asks,
                    ),
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
            if any(
                history_spec(v, self.clients[v].market(symbol)) != s
                for v, s in zip((long, short), specs)
            ):
                raise ValueError("FUNDING_INSTRUMENT_CHANGED")
            if self.book_history is not None:
                try:
                    count = await self.book_history.record(
                        [a[3], b[3]],
                        {v: {symbol: s} for v, s in zip((long, short), specs)},
                    )
                    if (
                        isinstance(count, bool)
                        or not isinstance(count, int)
                        or not 0 <= count <= 2
                    ):
                        raise ValueError("FUNDING_BOOK_RECORD_COUNT_INVALID")
                    self.book_recorded += count
                except Exception:
                    self.book_record_failures += 1
            if self.clock() - min(a[2], b[2]) > self.max_age:
                raise ValueError("FUNDING_BOOK_STALE_AFTER_RECORD")
            decision = self.clock()
            if not math.isfinite(decision) or decision < max(
                a[3].received_at, b[3].received_at
            ):
                raise ValueError("FUNDING_DECISION_TIME_INVALID")
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
                ts=decision,
                market_ts=min(a[2], b[2]),
                received_at=max(a[3].received_at, b[3].received_at),
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
        histories = {}
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
                histories[venue] = rows
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
        if self.history_store is not None and set(histories) == {p["buy"], p["sell"]}:
            try:
                await self.history_store.capture(
                    p, histories, self.clients, self.clock()
                )
            except Exception:
                # Offline evidence persistence does not rewrite Paper cashflow.
                self.history_store.failures += 1
        return History(
            verified,
            sum(x["amount"] for x in events),
            tuple(events),
            reason,
            until if verified else min(until, cutoff),
        )
