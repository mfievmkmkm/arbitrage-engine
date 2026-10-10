import asyncio, copy, sqlite3
from types import SimpleNamespace as NS
import pytest
from app.paper import PaperEngine
from app.db import Diary
from app.engine import Scanner, evaluate, Quote, vwap
from app.instruments import from_market
from app.discovery import RotatingUniverse
from app.funding_service import FundingService


def op():
    return dict(
        symbol="X/USDT:USDT",
        buy="binance",
        sell="bybit",
        notional=100,
        base_qty=1,
        entry_buy=100,
        entry_sell=110,
        executable=10,
        fee_pct=0.4,
        safety_pct=0.1,
    )


def test_realized_loss_and_entry_costs_limit_new_admissions(tmp_path):
    from app.bankroll_ledger import Ledger
    from app.spot_future_paper_engine import Engine as SpotFuture

    async def go():
        d = Diary(str(tmp_path / "d.db"))
        await d.init()
        ledger = Ledger(211)
        primary = PaperEngine(d, 211)
        primary.budget = lambda: ledger.equity
        assert primary.can_open(op())
        ledger.apply(-1)
        assert not primary.can_open(op())
        # Gross legs fit exactly, but entry commission and safety do not.
        ledger.realized = 0
        primary.capital = 210
        primary.budget = lambda: primary.capital
        assert not primary.can_open(op())
        sf = SpotFuture(capital=211)
        sf.budget = lambda: ledger.equity
        quote = dict(
            base="X",
            exchange="a",
            direction="LONG_SPOT_SHORT_FUTURE",
            notional=100,
            base_qty=1,
            fee_pct=0.4,
            safety_pct=0.1,
            prices=dict(spot_buy=100, spot_sell=99, future_buy=111, future_sell=110),
        )
        assert sf.can_open(quote)
        ledger.apply(-1)
        assert not sf.can_open(quote)

    asyncio.run(go())


def test_fixed_quantity_entry_cost_and_safety_survive_restart(tmp_path):
    async def go():
        d = Diary(str(tmp_path / "d.db"))
        await d.init()
        paper = PaperEngine(d, 500, target=99, max_seconds=99999)
        p = await paper.open(op())
        assert (
            p.entry_fees_usd == pytest.approx(0.21)
            and p.safety_usd == 0.1
            and paper.used_capital == pytest.approx(210.31)
        )
        restored = PaperEngine(d, 500, target=99, max_seconds=99999)
        await restored.restore()
        quote = dict(
            op(),
            exit_buy=102,
            exit_sell=106,
            exit_spread=10,
            fee_pct=0.8,
            safety_pct=99,
            funding_pct=999,
        )
        await restored.mark_and_exit([quote])
        p = restored.positions[1]
        assert p.current_net_usd == pytest.approx(6 - 0.21 - 0.416 - 0.1)
        assert p.last_mark["funding"] == 0 and p.safety_usd == 0.1
        quote["base_qty"] = 0.5
        before = p.current_net_usd
        await restored.mark_and_exit([quote])
        assert restored.positions[1].current_net_usd == before

    asyncio.run(go())


def test_failed_mark_does_not_change_memory_peak_and_failed_close_stays_open(tmp_path):
    async def go():
        d = Diary(str(tmp_path / "d.db"))
        await d.init()
        e = PaperEngine(d, 500, target=99, max_seconds=99999)
        p = await e.open(op())
        before = copy.deepcopy(p.__dict__)
        with sqlite3.connect(d.path) as c:
            c.execute(
                "CREATE TRIGGER fail BEFORE INSERT ON paper_marks BEGIN SELECT RAISE(ABORT,'mark write failed'); END"
            )
        with pytest.raises(Exception, match="mark write failed"):
            await e.mark_and_exit(
                [dict(op(), exit_buy=102, exit_sell=106, exit_spread=1)]
            )
        assert p.__dict__ == before
        with sqlite3.connect(d.path) as c:
            c.execute("DROP TRIGGER fail")
            c.execute(
                "CREATE TRIGGER close_fail BEFORE UPDATE ON paper_positions WHEN NEW.status='CLOSED' BEGIN SELECT RAISE(ABORT,'close failed'); END"
            )
        with pytest.raises(Exception, match="close failed"):
            await e.close(p.id)
        assert p.status == "OPEN" and p.id in e.positions
        restored = PaperEngine(d, 500)
        await restored.restore()
        assert restored.positions[p.id].status == "OPEN"

    asyncio.run(go())


@pytest.mark.parametrize("qty", [0, -1, float("nan"), float("inf")])
def test_depth_helpers_reject_nonfinite_or_nonpositive_quantity(qty):
    assert vwap([[100, 10]], qty) is None


@pytest.mark.parametrize(
    "level", [[100, -1], [float("nan"), 10], [0, 10], [100, float("inf")]]
)
def test_bad_levels_never_create_executable_price(level):
    assert vwap([level], 1) is None


def test_primary_watch_uses_saved_quantity_and_rejects_old_exchange_timestamp():
    async def go():
        symbol = "X/USDT:USDT"

        class Client:
            stale = False

            async def fetch_order_book(self, *a, **k):
                return dict(
                    bids=[[199, 10]],
                    asks=[[200, 10]],
                    **({"timestamp": 1000} if self.stale else {}),
                )

        scanner = Scanner(["binance", "bybit"], 10, 12)
        scanner.clients = {x: Client() for x in scanner.ids}
        scanner.symbols = {x: {symbol} for x in scanner.ids}
        market = dict(
            symbol=symbol,
            base="X",
            quote="USDT",
            settle="USDT",
            contract=True,
            linear=True,
            contractSize=1,
        )
        scanner.specs = {x: {symbol: from_market(x, market)} for x in scanner.ids}
        scanner.universe = RotatingUniverse(scanner.symbols)
        scanner.paused = True
        scanner.watch_routes = {(symbol, "binance", "bybit")}
        scanner.watch_positions = {
            (symbol, "binance", "bybit"): NS(base_qty=1, notional=100, entry_buy=100)
        }
        rows = await scanner.scan()
        assert rows[0]["base_qty"] == 1
        scanner.clients["bybit"].stale = True
        assert await scanner.scan() == []

    asyncio.run(go())


def test_funding_carry_counts_each_leg_calendar_independently(monkeypatch):
    import app.funding_timing as timing

    now = 1_000_000_000_000
    monkeypatch.setattr(timing.time, "time", lambda: now / 1000)
    service = FundingService({})

    async def get(v, s):
        return NS(
            rate=0.0001 if v == "a" else 0.0003,
            next_ts=now + 8 * 3600000 if v == "a" else now + 30 * 60000,
            interval_hours=8 if v == "a" else 1,
        )

    service.get = get
    amount, known, _ = asyncio.run(service.pair_carry_pct("a", "b", "X", 7200))
    assert known and amount == pytest.approx(0.06)
    assert not timing.window(now - 1, 100, 8, now).due
    assert not timing.window(now + 1000, 100, float("nan"), now).due
