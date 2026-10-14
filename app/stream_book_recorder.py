"""Bounded sampled public diary; persistence never blocks quote ingestion.

One latest snapshot per route per flush. Coalescing and dropped samples are
explicit counters. This is a sampled public tape, not every exchange event.
"""

import asyncio
from .engine import Quote
from .contract_book import to_base_levels


class Recorder:
    def __init__(self, store, specs, interval=1, max_routes=1000, sources=("WS",)):
        if not 0.1 <= interval <= 60 or type(max_routes) is not int or max_routes <= 0:
            raise ValueError("STREAM_RECORD_CONFIG_INVALID")
        if (
            not isinstance(sources, tuple)
            or not sources
            or any(s not in ("WS", "REST") for s in sources)
        ):
            raise ValueError("STREAM_RECORD_SOURCE_CONFIG_INVALID")
        self.sources = sources
        self.store, self.specs, self.interval, self.max_routes = (
            store,
            specs,
            interval,
            max_routes,
        )
        self.pending = {}
        self.task = None
        self.closed = False
        self.wake = asyncio.Event()
        self.coalesced = self.dropped = self.recorded = self.failures = 0

    def offer(self, venue, book):
        if self.closed:
            return
        try:
            symbol = book["symbol"]
            spec = self.specs[venue][symbol]
            if book["data_source"] not in self.sources:
                raise ValueError("STREAM_RECORD_SOURCE_INVALID")
            key = (venue, symbol)
            q = Quote(
                venue,
                symbol,
                to_base_levels(book["bids"], spec.contract_size),
                to_base_levels(book["asks"], spec.contract_size),
                book["timestamp"] / 1000,
                book["received_at"],
                book["data_source"],
            )
            if key in self.pending:
                self.coalesced += 1
            elif len(self.pending) >= self.max_routes:
                self.dropped += 1
                return
            self.pending[key] = q
        except (KeyError, TypeError, ValueError, OverflowError):
            self.dropped += 1

    def start(self):
        if self.closed:
            raise ValueError("STREAM_RECORDER_CLOSED")
        if self.task is None:
            self.task = asyncio.create_task(self._run())

    async def flush(self):
        rows = list(self.pending.values())
        self.pending.clear()
        if rows:
            try:
                count = await self.store.record(rows, self.specs)
                count = len(rows) if count is None else count
                if type(count) is not int or not 0 <= count <= len(rows):
                    raise ValueError("STREAM_RECORD_COUNT_INVALID")
                self.recorded += count
                self.dropped += len(rows) - count
            except Exception:
                # Never silently reuse a failed sample with a new receipt time.
                self.failures += 1
                self.dropped += len(rows)

    async def _run(self):
        while not self.closed:
            try:
                await asyncio.wait_for(self.wake.wait(), self.interval)
            except asyncio.TimeoutError:
                pass
            await self.flush()

    async def close(self):
        if self.closed:
            return
        self.closed = True
        self.wake.set()
        if self.task is not None:
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        await self.flush()

    def status(self):
        return dict(
            pending=len(self.pending),
            recorded=self.recorded,
            coalesced=self.coalesced,
            dropped=self.dropped,
            failures=self.failures,
        )
