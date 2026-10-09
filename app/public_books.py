"""Validated public books with bounded, supervised CCXT Pro subscriptions.

CCXT owns snapshot/delta reconstruction. This layer never stitches REST into a
stream and never grants private-stream or order-execution authority.
"""

import asyncio
import copy
import math
import time
from collections import Counter


def positive(value, reason):
    if isinstance(value, bool):
        raise ValueError(reason)
    try:
        value = float(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError(reason) from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError(reason)
    return value


def normalize(book, symbol, started, received, max_age, source="REST"):
    if source not in ("REST", "WS"):
        raise ValueError("PUBLIC_BOOK_SOURCE_INVALID")
    if not isinstance(book, dict) or book.get("symbol") not in (None, symbol):
        raise ValueError("PUBLIC_BOOK_SYMBOL_MISMATCH")
    if source == "WS" and (
        book.get("symbol") != symbol or book.get("timestamp") is None
    ):
        raise ValueError("PUBLIC_STREAM_IDENTITY_OR_TIME_MISSING")
    stamp = book.get("timestamp")
    ts = (
        started if stamp is None else positive(stamp, "PUBLIC_BOOK_TIME_INVALID") / 1000
    )
    if (
        not all(math.isfinite(t) and t > 0 for t in (ts, started, received))
        or received < started
        or ts > received
        or received - ts > max_age
        or (source == "REST" and received - started > max_age)
    ):
        raise ValueError("PUBLIC_BOOK_STALE")
    out = {}
    for side in ("bids", "asks"):
        rows = book.get(side)
        if not isinstance(rows, (list, tuple)) or not rows:
            raise ValueError("PUBLIC_BOOK_EMPTY")
        levels = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                raise ValueError("PUBLIC_BOOK_LEVEL_INVALID")
            p = positive(row[0], "PUBLIC_BOOK_PRICE_INVALID")
            q = positive(row[1], "PUBLIC_BOOK_AMOUNT_INVALID")
            if levels and (
                p >= levels[-1][0] if side == "bids" else p <= levels[-1][0]
            ):
                raise ValueError("PUBLIC_BOOK_ORDER_INVALID")
            levels.append([p, q])
        out[side] = levels
    if out["bids"][0][0] >= out["asks"][0][0]:
        raise ValueError("PUBLIC_BOOK_CROSSED")
    out.update(
        symbol=symbol,
        timestamp=ts * 1000,
        nonce=book.get("nonce"),
        data_source=source,
        received_at=received,
        requested_at=started,
    )
    return out


class Client:
    """Public-only facade; all ordinary metadata/funding methods delegate.

    Subscriptions are sticky until close. Their hard cap bounds even exchanges
    without an unsubscribe API. Overflow symbols use REST, not uncapped sockets.
    Only consumers' requested symbols subscribe; an idle task waits for updates.
    """

    def __init__(
        self,
        client,
        streams=False,
        max_symbols=40,
        max_age=12,
        stream_age=1.5,
        timeout=8,
        retry_delay=1,
        clock=time.time,
        monotonic=time.monotonic,
    ):
        if type(max_symbols) is not int or not 1 <= max_symbols <= 200:
            raise ValueError("PUBLIC_STREAM_CAP_INVALID")
        for value in (max_age, stream_age, timeout, retry_delay):
            positive(value, "PUBLIC_BOOK_CONFIG_INVALID")
        self.raw = client
        self.streams = bool(
            streams and getattr(client, "has", {}).get("watchOrderBook") is True
        )
        self.max_symbols, self.max_age, self.stream_age = (
            max_symbols,
            max_age,
            stream_age,
        )
        self.timeout, self.retry_delay, self.clock, self.monotonic = (
            timeout,
            retry_delay,
            clock,
            monotonic,
        )
        self.tasks, self.cache, self.errors = {}, {}, {}
        self.counters = Counter()
        self.closed = False
        self.on_book = None
        self._close_lock = asyncio.Lock()

    def __getattr__(self, name):
        return getattr(self.raw, name)

    def _cached(self, symbol):
        item = self.cache.get(symbol)
        if item is None:
            return None
        book, tick = item
        now, elapsed = self.clock(), self.monotonic() - tick
        if not (
            0 <= now - book["timestamp"] / 1000 <= self.stream_age
            and 0 <= now - book["received_at"] <= self.stream_age
            and 0 <= elapsed <= self.stream_age
        ):
            self.cache.pop(symbol, None)
            self.counters["stale"] += 1
            return None
        return book

    async def _watch(self, symbol):
        previous = None
        while not self.closed:
            try:
                started = self.clock()
                # No depth argument: allowed subscription depths vary by venue.
                raw = await asyncio.wait_for(
                    self.raw.watch_order_book(symbol), self.timeout
                )
                received = self.clock()
                book = normalize(raw, symbol, started, received, self.stream_age, "WS")
                nonce = book["nonce"]
                if nonce is not None:
                    if (
                        isinstance(nonce, bool)
                        or not isinstance(nonce, int)
                        or nonce < 0
                    ):
                        raise ValueError("PUBLIC_STREAM_SEQUENCE_INVALID")
                if previous is not None:
                    if book["timestamp"] < previous["timestamp"]:
                        raise ValueError("PUBLIC_STREAM_TIME_REGRESSION")
                    old = previous["nonce"]
                    if nonce is not None and old is not None and nonce < old:
                        raise ValueError("PUBLIC_STREAM_SEQUENCE_REGRESSION")
                    if nonce is not None and nonce == old:
                        if any(book[k] != previous[k] for k in ("bids", "asks")):
                            raise ValueError("PUBLIC_STREAM_SEQUENCE_CONFLICT")
                        # A repeated cached update cannot renew receipt freshness.
                        self.counters["duplicates"] += 1
                        await asyncio.sleep(0.01)
                        continue
                previous = book
                self.cache[symbol] = (book, self.monotonic())
                self.errors.pop(symbol, None)
                self.counters["updates"] += 1
                if self.on_book is not None:
                    try:
                        self.on_book(copy.deepcopy(book))
                    except Exception:
                        # A diary failure cannot turn a good quote into a bad one.
                        self.counters["record_errors"] += 1
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.cache.pop(symbol, None)
                previous = None
                self.errors[symbol] = (
                    str(error)
                    if isinstance(error, ValueError)
                    else type(error).__name__
                )
                self.counters["stream_errors"] += 1
                await asyncio.sleep(self.retry_delay)

    async def fetch_order_book(self, symbol, limit=None, params=None):
        if self.closed:
            raise ValueError("PUBLIC_BOOK_CLIENT_CLOSED")
        if limit is not None and (type(limit) is not int or limit <= 0):
            raise ValueError("PUBLIC_BOOK_LIMIT_INVALID")
        # Non-default REST params may select a different book/channel.
        if self.streams and not params:
            if symbol not in self.tasks and len(self.tasks) < self.max_symbols:
                self.tasks[symbol] = asyncio.create_task(self._watch(symbol))
            book = self._cached(symbol)
            if book is not None:
                out = copy.deepcopy(book)
                if limit is not None:
                    out["bids"], out["asks"] = out["bids"][:limit], out["asks"][:limit]
                self.counters["ws_reads"] += 1
                return out
        started = self.clock()
        kwargs = {}
        if limit is not None:
            kwargs["limit"] = limit
        if params:
            kwargs["params"] = params
        raw = await asyncio.wait_for(
            self.raw.fetch_order_book(symbol, **kwargs), self.timeout
        )
        out = normalize(raw, symbol, started, self.clock(), self.max_age)
        self.counters["rest_reads"] += 1
        return out

    def book_status(self):
        fresh = sum(self._cached(symbol) is not None for symbol in list(self.cache))
        return dict(
            streams=self.streams,
            subscribed=len(self.tasks),
            cap=self.max_symbols,
            fresh=fresh,
            errors=len(self.errors),
            **dict(self.counters)
        )

    async def close(self):
        async with self._close_lock:
            if self.closed:
                return
            self.closed = True
            for task in self.tasks.values():
                task.cancel()
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
            self.tasks.clear()
            self.cache.clear()
            await self.raw.close()


def wrap(client):
    from .config import config

    return Client(
        client,
        streams=config.public_streams,
        max_symbols=config.public_stream_max_symbols,
        max_age=config.max_age,
        stream_age=config.public_stream_max_age,
    )
