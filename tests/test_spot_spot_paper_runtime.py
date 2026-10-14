import asyncio, json, sqlite3, copy
import pytest
from app.spot_spot_paper import Engine
from app.spot_spot_source import Source
from app.spot_future_history_replay import dataset
from app.db import Diary
from app.paper_ledger import restore
from app.bankroll_ledger import Ledger
from app.risk import RiskGuard

SEED = {"a": {"USDT": 120}, "b": {"USDT": 0, "assets": {"X": {"qty": 1, "price": 100}}}}


def quote(ts=1000, watch=False):
    return dict(
        symbol="X/USDT",
        buy="a",
        sell="b",
        base_qty=1,
        entry_buy=100,
        entry_sell=110,
        exit_buy=99,
        exit_sell=110,
        fee_pct=0.4,
        safety_pct=0.1,
        ts=ts,
        net=9.5,
        watch_only=watch,
    )


async def engine(tmp_path, seed=None, **kwargs):
    result = Engine(
        tmp_path / "d.db",
        seed if seed is not None else SEED,
        capital=500,
        clock=lambda: 1000,
        **kwargs
    )
    await result.init()
    return result


def test_inventory_entry_close_and_restart_preserve_cash_and_assets(tmp_path):
    async def go():
        e = await engine(tmp_path, max_age=60)
        await e.cycle([quote()])
        assert len(e.positions) == 1
        assert next(iter(e.positions.values()))["inventory_evidence"] == dict(
            mode="PREFUNDED_PAPER_INVENTORY",
            buy="a",
            sell="b",
            asset="X",
            buy_quote=120,
            sell_quote=0,
            sell_base=1,
        )
        assert e.state["balances"]["a"]["X"] == 1 and e.state["balances"]["b"]["X"] == 0
        assert e.state["balances"]["a"]["USDT"] == pytest.approx(19.8)
        restarted = await engine(tmp_path, max_age=60)
        assert restarted.positions == e.positions and restarted.state["next_id"] == 2
        restarted.clock = lambda: 1061
        closing = quote(1061, True)
        closing.update(exit_buy=102, exit_sell=106)
        closed = await restarted.cycle([closing, closing])
        assert len(closed) == 1 and not restarted.positions
        assert closed[0]["net"] == pytest.approx(5.482)
        balances = restarted.state["balances"]
        assert balances["a"]["X"] == 0 and balances["b"]["X"] == 1
        assert balances["a"]["USDT"] + balances["b"]["USDT"] == pytest.approx(125.482)
        rows, excluded = await dataset(str(tmp_path / "d.db"), strategy="spot_spot")
        assert len(rows) == 1 and not excluded
        again = await engine(tmp_path, max_age=60)
        assert not again.positions and again.state["realized"] == pytest.approx(5.482)

    asyncio.run(go())


@pytest.mark.parametrize("case", ["empty", "quote", "base", "budget", "venue", "edge"])
def test_no_synthetic_short_or_unfunded_entry(tmp_path, case):
    async def go():
        seed = copy.deepcopy(SEED)
        if case == "empty":
            seed = {}
        if case == "quote":
            seed["a"]["USDT"] = 10
        if case == "base":
            seed["b"]["assets"]["X"]["qty"] = 0.1
        e = await engine(tmp_path, seed)
        if case == "budget":
            e.external_reserved = lambda: 300
        if case == "venue":
            e.allow_open = lambda x: False
        x = quote()
        if case == "edge":
            x["net"] = 0
            x["entry_sell"] = 100.5
        before = copy.deepcopy(e.state["balances"])
        await e.cycle([x])
        assert not e.positions and e.state["balances"] == before
        with sqlite3.connect(e.path) as d:
            assert (
                d.execute(
                    "SELECT reason FROM spot_spot_decisions ORDER BY id DESC LIMIT 1"
                ).fetchone()[0]
                != "PAPER_OPEN"
            )

    asyncio.run(go())


def test_insufficient_restore_cash_keeps_position_open_with_incident(tmp_path):
    async def go():
        e = await engine(tmp_path, max_age=60)
        await e.cycle([quote()])
        e.clock = lambda: 1061
        x = quote(1061, True)
        x["exit_sell"] = 200
        assert not await e.cycle([x]) and e.positions
        with sqlite3.connect(e.path) as d:
            assert (
                d.execute(
                    "SELECT COUNT(*) FROM spot_spot_decisions WHERE reason='EXIT_INVENTORY_LOW'"
                ).fetchone()[0]
                == 1
            )
        assert e.state["balances"]["b"]["X"] == 0

    asyncio.run(go())


def test_version_lease_blocks_second_process_without_duplicate_fills(tmp_path):
    async def go():
        one = await engine(tmp_path)
        two = await engine(tmp_path)
        await one.cycle([quote()])
        with pytest.raises(ValueError, match="CONCURRENT_WRITER"):
            await two.cycle([quote()])
        assert not two.positions
        with sqlite3.connect(one.path) as d:
            assert d.execute("SELECT COUNT(*) FROM spot_spot_paper").fetchone()[0] == 1

    asyncio.run(go())


def test_persisted_seed_cannot_be_reset_and_budget_is_validated(tmp_path):
    async def go():
        e = await engine(tmp_path)
        other = copy.deepcopy(SEED)
        other["a"]["USDT"] = 130
        with pytest.raises(ValueError, match="SEED_CONFLICT"):
            await engine(tmp_path, other)
        with pytest.raises(ValueError, match="EXCEEDS_CAPITAL"):
            await Engine(tmp_path / "other.db", SEED, capital=50).init()
        restored = await engine(tmp_path, {})
        assert restored.state["balances"] == e.state["balances"]

    asyncio.run(go())


@pytest.mark.parametrize(
    "case", ["nan", "qty", "stale", "same_venue", "negative_price"]
)
def test_bad_quote_never_changes_inventory(tmp_path, case):
    async def go():
        e = await engine(tmp_path)
        x = quote()
        if case == "nan":
            x["ts"] = float("nan")
        if case == "qty":
            x["base_qty"] = 0
        if case == "stale":
            x["ts"] = 800
        if case == "same_venue":
            x["sell"] = "a"
        if case == "negative_price":
            x["entry_sell"] = -1
        before = copy.deepcopy(e.state["balances"])
        await e.cycle([x])
        assert not e.positions and before == e.state["balances"]

    asyncio.run(go())


def test_disabled_discovery_still_quotes_original_route_and_saved_quantity(monkeypatch):
    import app.spot_spot_source as module

    monkeypatch.setattr(module.time, "time", lambda: 1000)

    class Client:
        def __init__(self, bid, ask):
            self.bid = bid
            self.ask = ask

        async def fetch_order_book(self, symbol):
            return dict(bids=[[self.bid, 10]], asks=[[self.ask, 10]], timestamp=1000000)

    async def go():
        source = Source({"a": Client(200, 201), "b": Client(190, 191)}, 10)
        source.allowed_venue = lambda v: False
        source.watch_routes = [dict(symbol="X/USDT", buy="a", sell="b", base_qty=1)]
        rows = await source.scan_symbol("X/USDT")
        assert len(rows) == 1 and rows[0]["watch_only"] and rows[0]["base_qty"] == 1
        assert rows[0]["buy"] == "a" and rows[0]["sell"] == "b" and rows[0]["net"] < 0

    asyncio.run(go())


def test_closed_spot_net_restores_shared_paper_equity_and_daily_risk(tmp_path):
    async def go():
        e = await engine(tmp_path, max_age=60)
        await e.cycle([quote()])
        e.clock = lambda: 1061
        x = quote(1061, True)
        x.update(exit_buy=102, exit_sell=106)
        await e.cycle([x])
        d = Diary(e.path)
        await d.init()
        ledger = Ledger(500)
        risk = RiskGuard(500)
        await restore(e.path, ledger, risk)
        assert ledger.equity == pytest.approx(505.482)

    asyncio.run(go())


def test_failed_mark_write_rolls_back_balances_position_and_version(tmp_path):
    async def go():
        e = await engine(tmp_path)
        before = copy.deepcopy(e.state)
        with sqlite3.connect(e.path) as d:
            d.execute(
                "CREATE TRIGGER fail_mark BEFORE INSERT ON spot_spot_marks BEGIN SELECT RAISE(ABORT,'test write failure'); END"
            )
        with pytest.raises(Exception, match="test write failure"):
            await e.cycle([quote()])
        assert e.state == before and e.version == 0
        reloaded = await engine(tmp_path)
        assert reloaded.state == before and reloaded.version == 0
        with sqlite3.connect(e.path) as d:
            assert d.execute("SELECT COUNT(*) FROM spot_spot_paper").fetchone()[0] == 0

    asyncio.run(go())


def test_notification_failure_cannot_undo_or_duplicate_committed_close(tmp_path):
    async def go():
        e = await engine(tmp_path, max_age=60)
        await e.cycle([quote()])
        e.clock = lambda: 1061

        async def fail(p):
            raise RuntimeError("notification offline")

        e.on_closed = fail
        x = quote(1061, True)
        x.update(exit_buy=102, exit_sell=106)
        with pytest.raises(RuntimeError):
            await e.cycle([x])
        assert not e.positions and e.state["realized"] == pytest.approx(5.482)
        e.on_closed = None
        assert not await e.cycle([x])
        with sqlite3.connect(e.path) as d:
            assert (
                d.execute(
                    "SELECT COUNT(*) FROM spot_spot_paper WHERE status!='OPEN'"
                ).fetchone()[0]
                == 1
            )

    asyncio.run(go())


def test_positive_results_do_not_prevent_inventory_restoration(tmp_path):
    async def go():
        e = await engine(tmp_path, max_age=60)
        await e.cycle([quote()])
        e.clock = lambda: 1061
        x = quote(1061, True)
        x.update(exit_buy=102, exit_sell=106)
        await e.cycle([x])
        restored = Engine(e.path, capital=220)
        await restored.init()
        assert restored.allocated_capital == 220 and restored.used_capital > 220
        assert restored.state["realized"] == pytest.approx(5.482)

    asyncio.run(go())


def test_service_paused_discovery_still_closes_saved_inventory_route(
    tmp_path, monkeypatch
):
    from app.spot_spot_service import Service
    import app.spot_spot_source as module

    clock = [1000]
    monkeypatch.setattr(module.time, "time", lambda: clock[0])

    class Client:
        def __init__(self, venue):
            self.venue = venue

        async def fetch_order_book(self, symbol):
            bid, ask = (
                ((99, 100) if self.venue == "a" else (110, 111))
                if clock[0] == 1000
                else ((102, 103) if self.venue == "a" else (105, 106))
            )
            return dict(bids=[[bid, 10]], asks=[[ask, 10]], timestamp=clock[0] * 1000)

    async def go():
        e = Engine(
            tmp_path / "d.db", SEED, capital=500, max_age=60, clock=lambda: clock[0]
        )
        await e.init()
        service = Service(
            {"a": Client("a"), "b": Client("b")}, ["X/USDT"], 100, paper=e
        )
        await service.cycle()
        assert len(e.positions) == 1
        clock[0] = 1061
        service.entry_enabled = False
        service.source.allowed_venue = lambda v: False
        assert await service.cycle() == [] and not e.positions
        assert e.state["realized"] == pytest.approx(5.691)

    asyncio.run(go())


def test_empty_research_account_can_be_allocated_once_without_resetting_traded_state(
    tmp_path,
):
    async def go():
        empty = await engine(tmp_path, {})
        await empty.cycle([quote()])
        funded = await engine(tmp_path, SEED)
        assert funded.allocated_capital == 220
        await funded.cycle([quote()])
        assert funded.positions
        with pytest.raises(ValueError, match="CONCURRENT_WRITER"):
            await empty.cycle([quote()])
        other = copy.deepcopy(SEED)
        other["a"]["USDT"] = 121
        with pytest.raises(ValueError, match="SEED_CONFLICT"):
            await engine(tmp_path, other)

    asyncio.run(go())
