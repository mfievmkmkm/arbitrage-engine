"""Public transport tests with local queues, no exchange sessions or orders."""

import asyncio
import copy
import json
from collections import defaultdict
from types import SimpleNamespace as NS
import pytest
from app.public_books import Client
from app.book_priority_runtime import live
from tests.test_public_books import book as base_book, until

X, Y, Z = "X/USDT:USDT", "Y/USDT:USDT", "Z/USDT:USDT"


def book(**changes):
    return dict(base_book(), **changes)


class Raw:
    has = {"watchOrderBook": True, "unWatchOrderBook": True}

    def __init__(self):
        self.queues = defaultdict(asyncio.Queue)
        self.subscribed = set()
        self.maximum = 0
        self.removed = []
        self.closed = 0
        self.error = False
        self.barrier = None

    async def watch_order_book(self, symbol):
        self.subscribed.add(symbol)
        self.maximum = max(self.maximum, len(self.subscribed))
        return await self.queues[symbol].get()

    async def un_watch_order_book(self, symbol):
        self.removed.append(symbol)
        if self.barrier:
            await self.barrier.wait()
        if self.error:
            raise TimeoutError()
        self.subscribed.discard(symbol)
        return {}

    async def fetch_order_book(self, symbol, **kwargs):
        return copy.deepcopy(book(symbol=symbol))

    async def close(self):
        self.subscribed.clear()
        self.closed += 1


def client(raw, cap=1):
    return Client(
        raw,
        streams=True,
        max_symbols=cap,
        clock=lambda: 100,
        timeout=0.05,
        retry_delay=0.01,
    )


def test_live_pin_displaces_candidate_only_after_successful_unwatch():
    async def run():
        r = Raw()
        c = client(r)
        try:
            c.set_book_priorities("scan", candidates=[X, Y])
            await until(lambda: r.subscribed == {X})
            r.barrier = asyncio.Event()
            c.set_book_priorities("live", required=[Y], priority=0)
            await until(lambda: r.removed == [X])
            assert set(c.tasks) == {X} and r.subscribed == {X}
            assert (await c.fetch_order_book(Y))["data_source"] == "REST"
            r.barrier.set()
            await until(lambda: r.subscribed == {Y})
            assert set(c.tasks) == {Y} and r.maximum == 1
            c.set_book_priorities("scan", candidates=[Z])
            await until(lambda: c._priority_task.done())
            assert set(c.tasks) == {Y}
            c.set_book_priorities("live")
            await until(lambda: r.subscribed == {Z})
            assert r.maximum == 1 and c.book_status()["rotated"] == 2
        finally:
            await c.close()
        assert r.closed == 1 and not c.tasks

    asyncio.run(run())


@pytest.mark.parametrize("support", [False, "emulated", True])
def test_unsupported_or_uncertain_unsubscribe_never_releases_capacity(support):
    async def run():
        r = Raw()
        r.has = dict(r.has, unWatchOrderBook=support)
        r.error = True
        c = client(r)
        try:
            c.set_book_priorities("scan", candidates=[X])
            await until(lambda: r.subscribed == {X})
            c.set_book_priorities("live", required=[Y], priority=0)
            await until(lambda: c._priority_task.done())
            for _ in range(3):
                assert (await c.fetch_order_book(Y))["data_source"] == "REST"
            assert set(c.tasks) == {X} and r.maximum == 1
            assert c.book_status()["retained"] == int(support is True)
            assert len(r.removed) == int(support is True)
            c.set_book_priorities("live", required=[Z], priority=0)
            await until(lambda: c._priority_task.done())
            assert len(r.removed) == int(support is True)
        finally:
            await c.close()

    asyncio.run(run())


def test_latest_owner_update_wins_during_inflight_rotation():
    async def run():
        r = Raw()
        c = client(r)
        try:
            c.set_book_priorities("scan", candidates=[X])
            await until(lambda: r.subscribed == {X})
            r.barrier = asyncio.Event()
            c.set_book_priorities("scan", candidates=[Y])
            await until(lambda: r.removed == [X])
            c.set_book_priorities("scan", candidates=[Z])
            r.barrier.set()
            await until(lambda: r.subscribed == {Z})
            assert Y not in c.tasks and r.maximum == 1
        finally:
            await c.close()

    asyncio.run(run())


def test_demand_multiple_owners_and_excess_required_use_rest():
    async def run():
        r = Raw()
        c = client(r, cap=2)
        try:
            c.set_book_priorities("paper", required=[X], candidates=[Z])
            c.set_book_priorities("live", required=[Y], priority=0)
            await until(lambda: r.subscribed == {X, Y})
            assert c._desired == (Y, X)
            c.set_book_priorities("paper", required=[X], candidates=[Z])
            c.set_book_priorities("live", required=[Y, Z, X], priority=0)
            await until(lambda: r.subscribed == {Y, Z})
            assert (await c.fetch_order_book(X))["data_source"] == "REST"
            assert r.maximum <= 2
        finally:
            await c.close()

    asyncio.run(run())


def test_rotation_invalidates_cache_and_fresh_update_required():
    async def run():
        r = Raw()
        c = client(r)
        try:
            c.set_book_priorities("scan", candidates=[X])
            await until(lambda: r.subscribed == {X})
            await r.queues[X].put(book())
            await until(lambda: X in c.cache)
            assert (await c.fetch_order_book(X))["data_source"] == "WS"
            c.set_book_priorities("scan", candidates=[Y])
            await until(lambda: r.subscribed == {Y})
            assert X not in c.cache and Y not in c.cache
            assert (await c.fetch_order_book(Y))["data_source"] == "REST"
            await r.queues[Y].put(book(symbol=Y, timestamp=90_000))
            await until(lambda: c.counters["stream_errors"] == 1)
            assert Y not in c.cache
            await r.queues[Y].put(book(symbol=Y))
            await until(lambda: Y in c.cache)
            assert (await c.fetch_order_book(Y))["data_source"] == "WS"
        finally:
            await c.close()

    asyncio.run(run())


def test_close_cancels_inflight_unsubscribe_without_task_leak():
    async def run():
        r = Raw()
        c = client(r)
        c.set_book_priorities("scan", candidates=[X])
        await until(lambda: r.subscribed == {X})
        r.barrier = asyncio.Event()
        c.set_book_priorities("scan", candidates=[Y])
        await until(lambda: r.removed == [X])
        await c.close()
        assert c._priority_task.done() and not c.tasks and not c._retained
        assert not r.subscribed and r.closed == 1
        c.set_book_priorities("scan", candidates=[Z])
        await c.close()
        assert r.closed == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "changes",
    [
        dict(owner=""),
        dict(priority=True),
        dict(priority=2),
        dict(required="X"),
        dict(candidates=[None]),
    ],
)
def test_invalid_priority_does_not_change_existing_demand(changes):
    async def run():
        c = client(Raw())
        try:
            c.set_book_priorities("scan", candidates=[X])
            with pytest.raises(ValueError):
                c.set_book_priorities(
                    **dict(dict(owner="scan", candidates=[Y]), **changes)
                )
            assert c._desired == (X,)
        finally:
            await c.close()

    asyncio.run(run())


def test_durable_live_pins_all_strategies_and_clears_after_close():
    demands = {}

    def recorder(name):
        return NS(
            set_book_priorities=lambda owner, required, candidates, priority: demands.update(
                {name: (required, candidates, priority)}
            )
        )

    d = {v: recorder("d:" + v) for v in ("a", "b")}
    s = {v: recorder("s:" + v) for v in ("a", "b")}

    def row(strategy, **meta):
        return dict(
            symbol=X,
            long_venue="a",
            short_venue="b",
            payload=json.dumps(dict(strategy=strategy, **meta)),
        )

    live(
        [
            row("futures_futures"),
            row(
                "spot_futures",
                cash_plan=dict(venue="a", future_symbol=Y, spot_symbol="Y/USDT"),
            ),
            row("spot_spot", spot_spot_plan=dict(venues=["a", "b"], symbol="Z/USDT")),
            row("cex_dex", dex_live_plan=dict(venue="b", symbol=Z)),
        ],
        d,
        s,
    )
    assert demands["d:a"] == ([X, Y], [], 0)
    assert demands["d:b"] == ([X, Z], [], 0)
    assert demands["s:a"] == (["Y/USDT", Y, "Z/USDT"], [], 0)
    assert demands["s:b"] == (["Z/USDT", Z, "ETH/USDT"], [], 0)
    live([], d, s)
    assert all(x == ([], [], 0) for x in demands.values())


def test_bad_durable_payload_keeps_previous_live_demand():
    calls = []
    c = NS(set_book_priorities=lambda *args, **kwargs: calls.append(args))
    with pytest.raises(KeyError):
        live([dict(payload='{"strategy":"cex_dex"}')], {"a": c}, {})
    assert not calls


def test_equal_rank_candidates_share_slots_between_scanners():
    async def run():
        r = Raw()
        c = client(r, cap=3)
        try:
            c.set_book_priorities("a", candidates=[X, Y, Z])
            c.set_book_priorities("b", candidates=["A/USDT", "B/USDT"])
            c.set_book_priorities("c", candidates=["C/USDT"])
            await until(lambda: len(r.subscribed) == 3)
            assert c._desired == (X, "A/USDT", "C/USDT")
            assert r.maximum <= 3
        finally:
            await c.close()

    asyncio.run(run())


@pytest.mark.parametrize("reply", [False, {"success": False}, {"error": "rejected"}])
def test_negative_unwatch_acknowledgment_keeps_network_slot(reply):
    async def run():
        r = Raw()

        async def rejected(symbol):
            r.removed.append(symbol)
            return reply

        r.un_watch_order_book = rejected
        c = client(r)
        try:
            c.set_book_priorities("scan", candidates=[X])
            await until(lambda: r.subscribed == {X})
            c.set_book_priorities("live", required=[Y], priority=0)
            await until(lambda: c._priority_task.done())
            assert set(c.tasks) == {X} and c._retained == {X}
            assert (
                r.maximum == 1
                and (await c.fetch_order_book(Y))["data_source"] == "REST"
            )
        finally:
            await c.close()

    asyncio.run(run())


def test_paused_futures_scanner_keeps_only_position_demand():
    async def run():
        from app.engine import Scanner
        from app.instruments import from_market
        from tests.test_native_order_plan import client as native_client, SYMBOL

        demands = []
        clients = {v: native_client() for v in ("binance", "bybit")}

        async def fetch(*args, **kwargs):
            return dict(bids=[[99, 10000]], asks=[[100, 10000]])

        for v, c in clients.items():
            c.fetch_order_book = fetch
            c.set_book_priorities = (
                lambda owner, required, candidates, priority, v=v: demands.append(
                    (v, required, candidates)
                )
            )
        scan = Scanner(list(clients), 5, 12)
        scan.clients = clients
        scan.symbols = {v: {SYMBOL} for v in clients}
        scan.specs = {
            v: {SYMBOL: from_market(v, c.market(SYMBOL))} for v, c in clients.items()
        }
        scan.paused = True
        scan.watch_routes = {(SYMBOL, "binance", "bybit")}
        await scan.scan()
        assert demands == [("binance", [SYMBOL], []), ("bybit", [SYMBOL], [])]

    asyncio.run(run())
