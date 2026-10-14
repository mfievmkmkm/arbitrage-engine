import asyncio
import copy
import json
import sqlite3
from types import SimpleNamespace as NS

import pytest
from app.public_books import Client, normalize
from app.book_freshness import check
from app.book_history import Store
from app.engine import Quote
from app.execution_book_replay import Tape
from app.instruments import from_market
from app.tg_system_center import render

SYMBOL = "X/USDT:USDT"


def book(**changes):
    return dict(
        symbol=SYMBOL,
        timestamp=100_000,
        nonce=1,
        bids=[[99, 10]],
        asks=[[100, 10]],
        **changes,
    )


class Raw:
    has = {"watchOrderBook": True}
    markets = {SYMBOL: {"contractSize": 0.1}}

    def __init__(self):
        self.events = asyncio.Queue()
        self.rest = book()
        self.rest_count = self.watch_count = self.closed_count = 0

    async def watch_order_book(self, symbol):
        self.watch_count += 1
        value = await self.events.get()
        if isinstance(value, Exception):
            raise value
        return value

    async def fetch_order_book(self, symbol, **kwargs):
        self.rest_count += 1
        return copy.deepcopy({**self.rest, "symbol": symbol})

    async def close(self):
        self.closed_count += 1


async def until(predicate):
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0.002)
    raise AssertionError("background stream did not reach expected state")


@pytest.mark.parametrize(
    "change",
    [
        {"symbol": "Y/USDT:USDT"},
        {"timestamp": float("nan")},
        {"timestamp": True},
        {"timestamp": 101_000},
        {"timestamp": 90_000},
        {"bids": []},
        {"bids": [[99, 0]]},
        {"bids": [[99, -1]]},
        {"bids": [[float("inf"), 1]]},
        {"asks": [[100, True]]},
        {"bids": [[98, 1], [99, 1]]},
        {"asks": [[100, 1], [100, 2]]},
        {"asks": [[98, 1]]},
        {"bids": [[100, 1]]},
    ],
)
def test_rejects_unusable_books(change):
    b = {**book(), **change}
    with pytest.raises(ValueError):
        normalize(b, SYMBOL, 100, 100.1, 1.5)


def test_rest_without_timestamp_is_timed_from_request_not_receipt():
    b = {**book(), "timestamp": None}
    assert normalize(b, SYMBOL, 99, 100, 1.5)["timestamp"] == 99_000
    with pytest.raises(ValueError, match="STALE"):
        normalize(b, SYMBOL, 98, 100, 1.5)
    with pytest.raises(ValueError, match="MISSING"):
        normalize(b, SYMBOL, 99, 100, 1.5, "WS")


def test_ws_cache_copy_depth_and_rest_fallback_after_staleness():
    async def go():
        raw, now, tick = Raw(), [100.0], [100.0]
        c = Client(raw, streams=True, clock=lambda: now[0], monotonic=lambda: tick[0])
        try:
            assert (await c.fetch_order_book(SYMBOL))["data_source"] == "REST"
            await raw.events.put(book())
            await until(lambda: c.counters["updates"] == 1)
            out = await c.fetch_order_book(SYMBOL, limit=1)
            assert out["data_source"] == "WS" and raw.rest_count == 1
            out["bids"][0][0] = 123
            assert (await c.fetch_order_book(SYMBOL))["bids"][0][0] == 99
            now[0] = tick[0] = 102
            raw.rest["timestamp"] = 102_000
            assert (await c.fetch_order_book(SYMBOL))["data_source"] == "REST"
            assert c.book_status()["fresh"] == 0
        finally:
            await c.close()
        assert not c.tasks and raw.closed_count == 1
        await c.close()
        assert raw.closed_count == 1
        with pytest.raises(ValueError, match="CLOSED"):
            await c.fetch_order_book(SYMBOL)

    asyncio.run(go())


def test_duplicate_does_not_renew_receipt_time_and_conflict_invalidates():
    async def go():
        raw, now = Raw(), [100.0]
        c = Client(raw, streams=True, clock=lambda: now[0], retry_delay=0.01)
        try:
            await c.fetch_order_book(SYMBOL)
            await raw.events.put(book())
            await until(lambda: c.counters["updates"] == 1)
            now[0] = 100.5
            await raw.events.put(book())
            await until(lambda: c.counters["duplicates"] == 1)
            assert c.cache[SYMBOL][0]["received_at"] == 100
            await raw.events.put({**book(), "bids": [[98, 10]]})
            await until(lambda: c.counters["stream_errors"] == 1)
            assert not c.cache and "CONFLICT" in c.errors[SYMBOL]
            assert (await c.fetch_order_book(SYMBOL))["data_source"] == "REST"
        finally:
            await c.close()

    asyncio.run(go())


@pytest.mark.parametrize(
    "fault",
    [
        RuntimeError("disconnect"),
        {**book(), "timestamp": 99_000},
        {**book(), "timestamp": 100_100, "nonce": 0},
        {**book(), "timestamp": 100_100, "nonce": True},
    ],
)
def test_bad_stream_invalidates_then_recovers_on_new_snapshot(fault):
    async def go():
        raw, now = Raw(), [100.0]
        c = Client(raw, streams=True, clock=lambda: now[0], retry_delay=0.01)
        try:
            await c.fetch_order_book(SYMBOL)
            await raw.events.put(book())
            await until(lambda: c.counters["updates"] == 1)
            now[0] = 100.2
            await raw.events.put(fault)
            await until(lambda: c.counters["stream_errors"] == 1)
            assert not c.cache
            await raw.events.put({**book(), "timestamp": 100_200, "nonce": 2})
            await until(lambda: c.counters["updates"] == 2)
            assert (
                not c.errors
                and (await c.fetch_order_book(SYMBOL))["data_source"] == "WS"
            )
        finally:
            await c.close()

    asyncio.run(go())


def test_subscription_cap_params_unsupported_and_clock_rollback():
    async def go():
        raw, now, tick = Raw(), [100.0], [100.0]
        c = Client(
            raw,
            streams=True,
            max_symbols=1,
            clock=lambda: now[0],
            monotonic=lambda: tick[0],
        )
        try:
            await asyncio.gather(*(c.fetch_order_book(SYMBOL) for _ in range(8)))
            await c.fetch_order_book("Y/USDT:USDT")
            assert len(c.tasks) == 1
            await raw.events.put(book())
            await until(lambda: c.counters["updates"] == 1)
            assert (await c.fetch_order_book(SYMBOL, params={"type": "spot"}))[
                "data_source"
            ] == "REST"
            tick[0] = 102
            assert c._cached(SYMBOL) is None
            c.cache[SYMBOL] = (normalize(book(), SYMBOL, 100, 100, 1.5, "WS"), 100)
            now[0] = 99
            assert c._cached(SYMBOL) is None
        finally:
            await c.close()
        raw.has = {"watchOrderBook": "emulated"}
        c = Client(raw, streams=True, clock=lambda: 100)
        assert not c.streams
        await c.fetch_order_book(SYMBOL)
        assert not c.tasks
        await c.close()

    asyncio.run(go())


def test_stream_timeout_and_cancelled_consumer_do_not_leak_tasks():
    async def go():
        raw = Raw()
        c = Client(raw, streams=True, clock=lambda: 100, timeout=0.01, retry_delay=0.01)
        await c.fetch_order_book(SYMBOL)
        await until(lambda: c.counters["stream_errors"] > 0)
        assert not c.cache
        await c.close()
        assert not c.tasks

    asyncio.run(go())


def test_ws_history_keeps_receipt_time_and_replay_never_looks_ahead(tmp_path):
    async def go():
        path = str(tmp_path / "books.db")
        s = Store(path, clock=lambda: 101)
        await s.init()
        spec = from_market(
            "binance",
            dict(
                symbol=SYMBOL,
                base="X",
                quote="USDT",
                settle="USDT",
                contract=True,
                linear=True,
                contractSize=1,
            ),
        )
        q = Quote("binance", SYMBOL, [[99, 1]], [[100, 1]], 100, 100.5, "WS")
        await s.record([q], {"binance": {SYMBOL: spec}})
        with sqlite3.connect(path) as d:
            b = json.loads(d.execute("SELECT payload FROM market_books").fetchone()[0])
        assert b["mode"] == "PUBLIC_WS_BASE_UNITS" and b["received_at"] == 100.5
        tape = Tape([b])
        assert tape.at("binance", SYMBOL, 100.4, 1.5) is None
        assert tape.at("binance", SYMBOL, 100.5, 1.5)

    asyncio.run(go())


@pytest.mark.parametrize(
    "args",
    [
        (10, 11, 1500),
        (float("nan"), 10, 1500),
        (10, float("inf"), 1500),
        (10, 9, -1),
        (10, True, 1500),
    ],
)
def test_freshness_does_not_accept_future_or_invalid_time(args):
    assert not check(*args).ok


def test_telegram_reports_transport_without_claiming_execution_ready():
    c = NS(
        book_status=lambda: dict(
            streams=True,
            subscribed=2,
            fresh=1,
            cap=40,
            errors=1,
            ws_reads=3,
            rest_reads=2,
        )
    )
    scanner = NS(paused=False, clients={"binance": c}, ids=["binance"], last_scan=None)
    text = render(scanner, NS(counts=lambda: {}))
    assert "WebSocket" in text and "3 / 2" in text and "допуск реального исполнения" in text
