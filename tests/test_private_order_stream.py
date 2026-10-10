import asyncio
import copy
import json
import sqlite3
from types import SimpleNamespace as NS
import pytest
from app.private_order_stream import Streams, StreamReader


def row(**changes):
    r = dict(
        id="o1",
        symbol="X/USDT:USDT",
        side="buy",
        amount=10,
        filled=2,
        status="open",
        average=100,
        fee={"cost": 0.1, "currency": "USDT"},
        clientOrderId="client1",
        info={"secret": "NEVER_SAVE"},
    )
    r.update(changes)
    return r


def test_events_are_durable_sanitized_and_cannot_regress_fills(tmp_path):
    async def go():
        now = [100]
        calls = []
        c = NS(has={})
        s = Streams(
            {"a": c},
            tmp_path / "events.db",
            lambda: calls.append(1),
            clock=lambda: now[0],
        )
        await s.init()
        await s.ingest("a", [row()])
        assert s.get("a", "o1", "X/USDT:USDT").filled == 2
        await s.ingest("a", [row(filled=1)])
        assert s.get("a", "o1", "X/USDT:USDT").filled == 2
        now[0] = 101
        await s.ingest("a", [row()])
        assert s.cache[("a", "o1")][0] == 100
        now[0] = 103
        assert s.get("a", "o1", "X/USDT:USDT") is None
        with sqlite3.connect(s.path) as d:
            payloads = [
                r[0] for r in d.execute("SELECT payload FROM private_order_events")
            ]
        assert (
            len(payloads) == 1
            and "NEVER_SAVE" not in payloads[0]
            and "info" not in json.loads(payloads[0])
        )
        await s.close()

    asyncio.run(go())


def test_batch_regression_terminal_order_and_missing_fee_do_not_erase_evidence(
    tmp_path,
):
    async def go():
        s = Streams({"a": NS(has={})}, tmp_path / "events.db", clock=lambda: 100)
        await s.init()
        await s.ingest("a", [row(filled=10, status="closed"), row(filled=2)])
        assert s.get("a", "o1", "X/USDT:USDT").filled == 10
        await s.ingest("a", [row(filled=10, status="open", fee=None)])
        assert s.get("a", "o1", "X/USDT:USDT").status == "FILLED"
        assert s.get("a", "o1", "X/USDT:USDT").fee == 0.1
        await s.close()

    asyncio.run(go())


@pytest.mark.parametrize(
    "change",
    [
        {"symbol": None},
        {"id": None},
        {"side": "long"},
        {"filled": float("nan")},
        {"filled": 11},
        {"amount": True},
        {"filled": -1},
        {"amount": None},
    ],
)
def test_invalid_events_never_publish(tmp_path, change):
    async def go():
        s = Streams({"a": NS(has={})}, tmp_path / "events.db")
        await s.init()
        with pytest.raises((ValueError, TypeError)):
            await s.ingest("a", [row(**change)])
        assert not s.cache

    asyncio.run(go())


def test_scope_conflict_aborts_batch_before_publish(tmp_path):
    async def go():
        s = Streams({"a": NS(has={})}, tmp_path / "events.db", clock=lambda: 100)
        await s.init()
        await s.ingest("a", [row()])
        with pytest.raises(ValueError, match="CONFLICT"):
            await s.ingest("a", [row(side="sell")])
        assert s.get("a", "o1", "X/USDT:USDT").filled == 2

    asyncio.run(go())


def test_reader_falls_back_for_stale_unknown_fees_and_disconnect(tmp_path):
    async def go():
        calls = []

        async def fetch(order_id, symbol):
            calls.append(order_id)
            return row()

        c = NS(has={}, fetch_order=fetch)
        s = Streams({"a": c}, tmp_path / "events.db", clock=lambda: 100)
        await s.init()
        reader = StreamReader("a", c, s)
        await s.ingest("a", [row(fee=None)])
        assert (await reader.order("o1", "X/USDT:USDT")).filled == 2 and len(calls) == 1
        await s.ingest("a", [row()])
        assert (await reader.order("o1", "X/USDT:USDT")).fee == 0.1 and len(calls) == 1
        s.errors["a"] = "DISCONNECTED"
        await reader.order("o1", "X/USDT:USDT")
        assert len(calls) == 2
        assert s.get("a", "o1", "OTHER") is None
        await s.close()

    asyncio.run(go())


def test_worker_invalidates_on_disconnect_then_stops_without_leaks(tmp_path):
    async def go():
        queue = asyncio.Queue()

        async def watch():
            value = await queue.get()
            if isinstance(value, Exception):
                raise value
            return value

        c = NS(has={"watchOrders": True}, watch_orders=watch)
        s = Streams({"a": c}, tmp_path / "events.db", retry=0.01)
        await s.init()
        s.start()
        await queue.put([row()])
        for _ in range(100):
            if s.cache:
                break
            await asyncio.sleep(0.002)
        assert s.cache
        await queue.put(RuntimeError("disconnect"))
        for _ in range(100):
            if s.errors:
                break
            await asyncio.sleep(0.002)
        assert not s.cache and "a" in s.errors
        await s.close()
        assert not s.tasks

    asyncio.run(go())
