import asyncio
from types import SimpleNamespace as NS
import pytest
from app.stream_book_recorder import Recorder


def book(symbol="X", price=99):
    return dict(
        symbol=symbol,
        timestamp=100_000,
        received_at=100.1,
        data_source="WS",
        bids=[[price, 10]],
        asks=[[price + 1, 10]],
    )


class Store:
    def __init__(self):
        self.rows = []

    async def record(self, rows, specs):
        self.rows.extend(rows)


def test_coalescing_bound_and_contract_conversion():
    async def go():
        store = Store()
        specs = {"a": {"X": NS(contract_size=0.1), "Y": NS(contract_size=1)}}
        r = Recorder(store, specs, max_routes=1)
        r.offer("a", book())
        r.offer("a", book(price=98))
        r.offer("a", book("Y"))
        r.offer("unknown", book())
        assert r.status() == dict(
            pending=1, recorded=0, coalesced=1, dropped=2, failures=0
        )
        await r.close()
        q = store.rows[0]
        assert q.bids == [[98, 1]] and q.received_at == 100.1 and q.data_source == "WS"
        r.offer("a", book())
        assert not r.pending and r.recorded == 1
        await r.close()
        assert len(store.rows) == 1

    asyncio.run(go())


def test_failed_persistence_is_visible_and_never_retimestamps():
    async def go():
        class Broken:
            async def record(self, *args):
                raise RuntimeError("database unavailable")

        r = Recorder(Broken(), {"a": {"X": NS(contract_size=1)}})
        r.offer("a", book())
        await r.flush()
        assert r.failures == r.dropped == 1 and r.recorded == 0 and not r.pending
        r.store = Store()
        await r.flush()
        assert not r.store.rows
        await r.close()

    asyncio.run(go())


def test_shutdown_waits_for_inflight_database_write_and_flushes_pending():
    async def go():
        entered, release = asyncio.Event(), asyncio.Event()

        class Slow(Store):
            async def record(self, rows, specs):
                entered.set()
                await release.wait()
                await super().record(rows, specs)

        store = Slow()
        r = Recorder(store, {"a": {"X": NS(contract_size=1)}}, interval=0.1)
        r.start()
        r.offer("a", book())
        await asyncio.wait_for(entered.wait(), 1)
        r.offer("a", book(price=98))
        closing = asyncio.create_task(r.close())
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        await asyncio.wait_for(closing, 1)
        assert [q.bids[0][0] for q in store.rows] == [99, 98]
        assert r.recorded == 2 and r.task is None and not r.pending

    asyncio.run(go())


@pytest.mark.parametrize("interval", [0, float("nan"), 61])
def test_invalid_config(interval):
    with pytest.raises(ValueError):
        Recorder(Store(), {}, interval=interval)
