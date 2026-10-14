"""Secondary public tapes: local books/SQLite, no private APIs or exchange sends."""

import asyncio
import copy
import json
import sqlite3
import zipfile
from dataclasses import asdict
from types import SimpleNamespace as NS
import pytest
from app.secondary_book_history import spec, attach
from app.book_history import Store, valid_book
from app.public_books import Client
from app.execution_book_replay import Tape, simulate, Scenario
from app.audit_export import build as export
from tests.test_public_books import until

SPOT, FUTURE = "X/USDT", "X/USDT:USDT"


def market(symbol=SPOT, **changes):
    out = dict(
        symbol=symbol,
        base="X",
        quote="USDT",
        spot=symbol == SPOT,
        contract=symbol == FUTURE,
        linear=symbol == FUTURE,
        settle="USDT" if symbol == FUTURE else "",
        contractSize=0.1 if symbol == FUTURE else None,
    )
    return dict(out, **changes)


def book(symbol=SPOT, source="REST", **changes):
    return dict(
        dict(
            symbol=symbol,
            timestamp=100_000,
            received_at=100.1,
            data_source=source,
            bids=[[99, 10]],
            asks=[[100, 10]],
        ),
        **changes,
    )


class Raw:
    has = {"watchOrderBook": True}

    def __init__(self):
        self.markets = {s: market(s) for s in (SPOT, FUTURE)}
        self.events = asyncio.Queue()
        self.calls = []
        self.closed = False

    def market(self, symbol):
        return self.markets[symbol]

    async def fetch_order_book(self, symbol, **kwargs):
        self.calls.append(symbol)
        return dict(symbol=symbol, timestamp=100_000, bids=[[99, 10]], asks=[[100, 10]])

    async def watch_order_book(self, symbol):
        return await self.events.get()

    async def close(self):
        self.closed = True


def test_spot_and_contract_metadata_remain_explicit():
    assert asdict(spec("a", market()))["spot"] is True
    assert spec("a", market()).contract_size == 1
    f = asdict(spec("a", market(FUTURE)))
    assert f["contract"] and f["contract_size"] == 0.1 and not f.get("spot")


@pytest.mark.parametrize(
    "changes",
    [
        dict(quote="USDC"),
        dict(active=False),
        dict(contract=True),
        dict(contract=0),
        dict(contractSize=True),
        dict(contractSize=0.1),
        dict(linear=True),
        dict(settle="USDT"),
        dict(symbol=FUTURE),
        dict(spot=None),
        dict(base=None),
    ],
)
def test_ambiguous_spot_metadata_rejected(changes):
    with pytest.raises(ValueError):
        spec("a", dict(market(), **changes))


@pytest.mark.parametrize("size", [None, 0, -1, True, float("nan"), float("inf")])
def test_contract_size_never_defaults_to_one(size):
    with pytest.raises(ValueError):
        spec("a", market(FUTURE, contractSize=size))


def test_secondary_rest_and_ws_archive_preserves_units_and_receipt_time(tmp_path):
    async def run():
        store = Store(tmp_path / "db.sqlite", clock=lambda: 101)
        await store.init()
        raw = Raw()
        c = Client(raw, streams=True, clock=lambda: 100.1)
        recorder = attach(store, {"a": c})
        try:
            assert raw.calls == []
            result = await c.fetch_order_book(SPOT)
            result["bids"][0][1] = 999
            await recorder.flush()
            await raw.events.put(book(SPOT, "WS", timestamp=100_100, nonce=1))
            await until(lambda: c.counters["updates"] == 1)
            await recorder.flush()
            c.on_snapshot(book(FUTURE))
            await recorder.flush()
            with sqlite3.connect(store.path) as d:
                rows = [
                    json.loads(r[0])
                    for r in d.execute("SELECT payload FROM market_books ORDER BY id")
                ]
            assert len(rows) == recorder.recorded == 3
            assert rows[0]["mode"] == "PUBLIC_REST_BASE_UNITS"
            assert rows[1]["mode"] == "PUBLIC_WS_BASE_UNITS"
            assert (
                rows[0]["bids"] == [[99, 10]] and rows[0]["instrument"]["spot"] is True
            )
            assert rows[1]["received_at"] == 100.1
            assert (
                rows[2]["bids"] == [[99, 1]]
                and rows[2]["instrument"]["contract_size"] == 0.1
            )
            tape = Tape(rows)
            assert tape.at("a", SPOT, 100.05, 1.5) is None
            assert tape.at("a", SPOT, 100.1, 1.5)
            assert tape.at("a", FUTURE, 100.1, 1.5)
        finally:
            await c.close()
            await recorder.close()
        assert raw.closed and recorder.task is None

    asyncio.run(run())


def test_unknown_or_changed_metadata_is_counted_without_relabeling(tmp_path):
    async def run():
        store = Store(tmp_path / "db.sqlite", clock=lambda: 101)
        await store.init()
        raw = Raw()
        c = Client(raw, clock=lambda: 100.1)
        recorder = attach(store, {"a": c})
        try:
            c.on_snapshot(book("UNKNOWN/USDT"))
            raw.markets[FUTURE]["contractSize"] = 1
            c.on_snapshot(book(FUTURE))
            c.on_snapshot(book(SPOT))
            await recorder.flush()
            assert recorder.dropped == 2 and recorder.recorded == 1
            assert (await store.stats())["count"] == 1
        finally:
            await c.close()
            await recorder.close()

    asyncio.run(run())


def test_invalid_sample_never_counts_as_successful_persistence(tmp_path):
    async def run():
        store = Store(tmp_path / "db.sqlite", clock=lambda: 101)
        await store.init()
        c = Client(Raw(), clock=lambda: 100.1)
        recorder = attach(store, {"a": c})
        try:
            c.on_snapshot(book(SPOT, bids=[[float("nan"), 10]]))
            await recorder.flush()
            assert recorder.recorded == 0 and recorder.dropped == 1
            assert (await store.stats())["count"] == 0
        finally:
            await c.close()
            await recorder.close()

    asyncio.run(run())


def test_cash_books_cannot_be_used_as_futures_execution(tmp_path):
    async def run():
        store = Store(tmp_path / "db.sqlite", clock=lambda: 101)
        await store.init()
        from app.engine import Quote

        specs = {v: {SPOT: spec(v, market())} for v in ("a", "b")}
        await store.record(
            [Quote(v, SPOT, [[99, 10]], [[100, 10]], 100, 100) for v in specs], specs
        )
        with sqlite3.connect(store.path) as d:
            rows = [
                json.loads(r[0]) for r in d.execute("SELECT payload FROM market_books")
            ]
        result = simulate(
            dict(
                base_qty=1,
                opened_at=100,
                closed_at=100.2,
                buy="a",
                sell="b",
                symbol=SPOT,
                safety_usd=0,
            ),
            Tape(rows),
            Scenario("base", 0, 0),
            dict(a=0, b=0),
        )
        assert result["status"] == "NO_ENTRY", result
        assert all(r["filled"] == 0 for r in result["orders"])

    asyncio.run(run())


def test_rest_diary_failure_does_not_mutate_or_block_valid_book():
    async def run():
        c = Client(Raw(), clock=lambda: 100.1)

        def failed(book):
            book["bids"][0][0] = 0
            raise RuntimeError("offline diary failure")

        c.on_snapshot = failed
        try:
            result = await c.fetch_order_book(SPOT)
            assert result["bids"][0][0] == 99 and c.counters["record_errors"] == 1
        finally:
            await c.close()

    asyncio.run(run())


def test_secondary_tape_export_and_shared_retention(tmp_path):
    async def run():
        store = Store(tmp_path / "db.sqlite", max_rows=1, clock=lambda: 101)
        await store.init()
        from app.engine import Quote

        specs = {"a": {s: spec("a", market(s)) for s in (SPOT, FUTURE)}}
        assert (
            await store.record(
                [Quote("a", SPOT, [[99, 1]], [[100, 1]], 100, 100)], specs
            )
            == 1
        )
        assert (
            await store.record(
                [Quote("a", FUTURE, [[99, 1]], [[100, 1]], 100, 100)], specs
            )
            == 1
        )
        assert (await store.stats())["count"] == 1
        _, archive = await export(store.path, tmp_path / "export")
        with zipfile.ZipFile(archive) as z:
            names = z.namelist()
            assert "market_books.csv" in names

    asyncio.run(run())


def test_bundle_shutdown_flushes_last_secondary_snapshot(tmp_path):
    async def run():
        from app.secondary_bootstrap import Bundle

        store = Store(tmp_path / "db.sqlite", clock=lambda: 101)
        await store.init()
        c = Client(Raw(), clock=lambda: 100.1)

        async def stop():
            pass

        bundle = Bundle({"a": c}, NS(stop=stop), None)
        bundle.book_recorder = attach(store, bundle.clients)
        await c.fetch_order_book(SPOT)
        assert bundle.book_recorder.pending
        await bundle.close()
        assert bundle.book_recorder.closed and bundle.book_recorder.task is None
        assert (await store.stats())["count"] == 1
        assert c.raw.closed

    asyncio.run(run())


def test_system_shows_secondary_tape_and_combined_transport():
    from app.tg_system_center import render

    status = lambda: dict(streams=False, subscribed=0, fresh=0, errors=0, rest_reads=2)
    recorder = NS(status=lambda: dict(recorded=3, coalesced=1, dropped=2, failures=0))
    primary = NS(
        paused=False, clients={"a": NS(book_status=status)}, ids=["a"], last_scan=None
    )
    secondary = NS(clients={"a": NS(book_status=status)}, book_recorder=recorder)
    text = render(primary, NS(counts=lambda: {}), secondary)
    assert "История Spot/Futures и Spot/Spot: 3" in text and "0 / 4" in text
