import asyncio, copy, sqlite3
from types import SimpleNamespace as NS
import pytest
from app.funding_paper import Engine
from app.funding_paper_source import Source, History
from app.funding import FundingSnapshot
from app.funding_arb_scanner import evaluate
from app.funding_interval import infer
from app.funding_cache import FundingCache
from app.micro_live_acceptance import evaluate as acceptance
from app.db import Diary
from app.paper_ledger import restore
from app.bankroll_ledger import Ledger
from app.spot_future_history_replay import dataset

SYMBOL = "X/USDT:USDT"
CANDIDATE = {"symbol": SYMBOL, "long_venue": "a", "short_venue": "b", "carry_pct": 999}


class Feed:
    max_age = 1.5

    def __init__(self):
        self.now = 1000
        self.valid = True
        self.history_valid = False
        self.amount = 1.05
        self.price = 100

    async def quote(self, symbol, buy, sell, qty=None):
        if not self.valid:
            return {"ok": False, "reason": "OFFLINE"}
        return dict(
            ok=True,
            symbol=symbol,
            buy=buy,
            sell=sell,
            base_qty=1 if qty is None else qty,
            entry_buy=100,
            entry_sell=105,
            exit_buy=self.price,
            exit_sell=105,
            fee_pct=0.4,
            safety_pct=0.1,
            ts=self.now,
            long_rate=0,
            short_rate=0.01,
            long_interval=1,
            short_interval=1,
            long_next=1050,
            short_next=1050,
            mode="PAPER_MODEL",
        )

    async def settlements(self, p, until):
        return History(
            self.history_valid,
            self.amount if self.history_valid else 0,
            tuple(
                [dict(venue="b", ts=1050, amount=self.amount, rate=0.01)]
                if self.history_valid
                else []
            ),
            "VERIFIED_MODEL" if self.history_valid else "PENDING",
            until if self.history_valid else until - 30,
        )


async def engine(tmp_path, feed=None):
    feed = feed or Feed()
    e = Engine(
        tmp_path / "d.db", feed, capital=500, max_seconds=60, clock=lambda: feed.now
    )
    await e.init()
    return e, feed


def test_forecast_is_not_income_and_close_waits_for_history_after_restart(tmp_path):
    async def go():
        e, f = await engine(tmp_path)
        await e.cycle([CANDIDATE])
        assert len(e.positions) == 1
        f.now = 1030
        await e.cycle([])
        assert (
            e.positions["1"]["last_mark"]["funding"] == 0
            and e.positions["1"]["net"] < 0
        )
        f.now = 1061
        assert not await e.cycle([], False)
        assert e.positions["1"][
            "status"
        ] == "EXIT_ACCOUNTING_PENDING" and e.used_capital == pytest.approx(205.305)
        restarted, _ = await engine(tmp_path, f)
        f.now = 1100
        f.history_valid = True
        f.price = 999
        closed = await restarted.cycle([], False)
        assert len(closed) == 1 and closed[0]["net"] == pytest.approx(0.54)
        assert (
            closed[0]["exit_quote"]["exit_buy"] == 100 and restarted.used_capital == 0
        )
        assert not await restarted.cycle([], False)
        with sqlite3.connect(e.path) as d:
            assert (
                d.execute("SELECT COUNT(*) FROM funding_paper_events").fetchone()[0]
                == 1
            )
            assert (
                d.execute(
                    "SELECT COUNT(*) FROM funding_paper WHERE status='CLOSED'"
                ).fetchone()[0]
                == 1
            )
        await Diary(e.path).init()
        ledger = Ledger(500)
        await restore(e.path, ledger)
        assert ledger.equity == pytest.approx(500.54)
        rows, _ = await dataset(e.path, strategy="funding_arb")
        assert len(rows) == 1

    asyncio.run(go())


@pytest.mark.parametrize("case", ["risk", "budget", "calendar", "carry", "offline"])
def test_admission_checks_actual_inputs_not_displayed_carry(tmp_path, case):
    async def go():
        e, f = await engine(tmp_path)
        if case == "risk":
            e.allow_open = lambda x: False
        if case == "budget":
            e.external_reserved = lambda: 400
        if case == "calendar":
            f.now = 1060
        if case == "carry":
            e.min_carry = 99
        if case == "offline":
            f.valid = False
        await e.cycle([CANDIDATE])
        assert not e.positions

    asyncio.run(go())


def test_basis_loss_records_exit_without_fake_funding_or_release_of_reserve(tmp_path):
    async def go():
        e, f = await engine(tmp_path)
        await e.cycle([CANDIDATE])
        f.now = 1005
        f.price = 97
        await e.cycle([], False)
        assert e.positions["1"][
            "exit_reason"
        ] == "BASIS_LOSS_STOP" and e.used_capital == pytest.approx(205.305)
        assert e.positions["1"]["last_mark"]["funding"] == 0

    asyncio.run(go())


def test_stale_book_after_history_and_concurrent_writer_are_blocked(tmp_path):
    async def go():
        e, f = await engine(tmp_path)
        other, _ = await engine(tmp_path, f)
        await e.cycle([CANDIDATE])
        with pytest.raises(ValueError, match="CONCURRENT_WRITER"):
            await other.cycle([CANDIDATE])
        original = f.settlements

        async def slow(p, until):
            h = await original(p, until)
            f.now += 3
            return h

        f.settlements = slow
        f.now = 1061
        await e.cycle([])
        assert (
            e.positions["1"]["status"] == "OPEN"
            and e.positions["1"]["data_reason"] == "BOOK_STALE_AFTER_HISTORY"
        )

    asyncio.run(go())


def test_failed_funding_mark_transaction_rolls_back_state(tmp_path):
    async def go():
        e, f = await engine(tmp_path)
        await e.cycle([CANDIDATE])
        before = copy.deepcopy(e.state)
        with sqlite3.connect(e.path) as d:
            d.execute(
                "CREATE TRIGGER fail BEFORE INSERT ON funding_paper_marks BEGIN SELECT RAISE(ABORT,'write failure'); END"
            )
        f.now = 1030
        with pytest.raises(Exception, match="write failure"):
            await e.cycle([])
        assert e.state == before and e.pending == 0
        reloaded, _ = await engine(tmp_path, f)
        assert reloaded.state == before

    asyncio.run(go())


def test_direction_uses_common_horizon_and_intervals_parse_units():
    result = evaluate("a", "b", 0.08, 0.1, 1, 8, 0.01)
    assert result.long_venue == "b" and result.short_venue == "a"
    assert result.gross_carry_pct == pytest.approx(0.54)
    assert infer({"interval": "8h"}).hours == 8
    assert infer({"interval": "480m"}).hours == 8
    assert not infer({"interval": float("inf")}).known
    assert not infer({"info": {"fundingInterval": 480}}).known
    cache = FundingCache()
    cache.put("a", "x", 0.01, now=0)
    assert cache.get("a", "x", now=0) and cache.get("a", "x", now=-1) is None


@pytest.mark.parametrize("fake", [1, "true", "false", {}, [], None])
def test_release_acceptance_requires_exact_boolean_evidence(fake):
    required = (
        "ci",
        "compile",
        "unit",
        "entry_e2e",
        "exit_e2e",
        "restart_e2e",
        "unknown_order",
        "private_reconcile",
        "kill_switch",
        "daily_stop",
        "fee_verified",
        "funding_known",
        "withdraw_safe",
        "venue_capabilities",
    )
    results = {x: True for x in required}
    results["fee_verified"] = fake
    assert not acceptance(results).passed


class Client:
    has = {"fetchFundingRateHistory": True}

    def __init__(self, rate):
        self.rows = [dict(symbol=SYMBOL, timestamp=1050000, fundingRate=rate)]

    async def fetch_funding_rate_history(self, *args):
        return self.rows


def test_history_checks_sign_window_completeness_conflicts_and_maturity():
    async def go():
        clients = {"a": Client(0.001), "b": Client(0.01)}
        now = [1100]
        source = Source(clients, None, 5, clock=lambda: now[0])
        p = dict(
            symbol=SYMBOL,
            buy="a",
            sell="b",
            opened_at=1000,
            base_qty=1,
            entry_buy=100,
            entry_sell=105,
            long_next=1050,
            short_next=1050,
            long_interval=1,
            short_interval=1,
        )
        result = await source.settlements(p, 1061)
        assert result.verified and result.amount == pytest.approx(0.95)
        now[0] = 1061
        result = await source.settlements(p, 1061)
        assert not result.verified
        now[0] = 1100
        clients["a"].rows = []
        assert not (await source.settlements(p, 1061)).verified
        clients["a"].rows = [
            dict(symbol=SYMBOL, timestamp=1050000, fundingRate=0.001)
        ] * 100
        assert not (await source.settlements(p, 1061)).verified
        clients["a"].rows = [
            dict(symbol=SYMBOL, timestamp=1050000, fundingRate=0.001),
            dict(symbol=SYMBOL, timestamp=1050000, fundingRate=0.002),
        ]
        assert not (await source.settlements(p, 1061)).verified
        clients["a"].rows = [dict(symbol=SYMBOL, timestamp=1040000, fundingRate=0.001)]
        assert not (await source.settlements(p, 1061)).verified

    asyncio.run(go())


def test_shared_equity_cannot_spend_entry_costs(tmp_path):
    async def go():
        e, f = await engine(tmp_path)
        e.budget = lambda: 205
        await e.cycle([CANDIDATE])
        assert not e.positions
        e.budget = lambda: 206
        await e.cycle([CANDIDATE])
        assert e.used_capital == pytest.approx(205.305)

    asyncio.run(go())


def test_duplicate_history_timestamps_cannot_double_credit():
    async def go():
        clients = {"a": Client(0.001), "b": Client(0.01)}
        clients["b"].rows.append(
            dict(symbol=SYMBOL, timestamp=1050500, fundingRate=0.01)
        )
        source = Source(clients, None, 5, clock=lambda: 1100)
        p = dict(
            symbol=SYMBOL,
            buy="a",
            sell="b",
            opened_at=1000,
            base_qty=1,
            entry_buy=100,
            entry_sell=105,
            long_next=1050,
            short_next=1050,
            long_interval=1,
            short_interval=1,
        )
        result = await source.settlements(p, 1061)
        assert (
            result.verified
            and result.amount == pytest.approx(0.95)
            and len(result.events) == 2
        )
        clients["b"].rows[-1]["fundingRate"] = 0.02
        assert not (await source.settlements(p, 1061)).verified

    asyncio.run(go())


@pytest.mark.parametrize("case", ["same_venue", "old", "size", "interval", "depth"])
def test_public_quote_requires_fresh_compatible_executable_books(case):
    async def go():
        class BookClient:
            def market(self, symbol):
                return dict(
                    symbol=symbol,
                    base="X",
                    quote="USDT",
                    settle="USDT",
                    contract=True,
                    linear=True,
                    contractSize=0 if case == "size" else 2,
                )

            async def fetch_order_book(self, *args, **kwargs):
                return dict(
                    bids=[[99, 0 if case == "depth" else 10]],
                    asks=[[100, 10]],
                    timestamp=(990 if case == "old" else 1000) * 1000,
                )

        class Funding:
            async def get(self, *args):
                return NS(
                    rate=0.01,
                    interval_hours=0 if case == "interval" else 8,
                    next_ts=1050000,
                )

        source = Source(
            {"a": BookClient(), "b": BookClient()}, Funding(), 5, clock=lambda: 1000
        )
        result = await source.quote(SYMBOL, "a", "a" if case == "same_venue" else "b")
        assert not result["ok"]

    asyncio.run(go())


def test_public_quote_uses_saved_base_quantity_with_contract_books():
    async def go():
        class BookClient:
            def market(self, symbol):
                return dict(
                    symbol=symbol,
                    base="X",
                    quote="USDT",
                    settle="USDT",
                    contract=True,
                    linear=True,
                    contractSize=0.5,
                )

            async def fetch_order_book(self, *args, **kwargs):
                return dict(
                    bids=[[99, 1], [98, 1]],
                    asks=[[100, 1], [102, 1]],
                    timestamp=1000000,
                )

        class Funding:
            async def get(self, *args):
                return NS(rate=0.01, interval_hours=8, next_ts=1050000)

        source = Source(
            {"a": BookClient(), "b": BookClient()}, Funding(), 5, clock=lambda: 1000
        )
        result = await source.quote(SYMBOL, "a", "b", qty=1)
        assert (
            result["ok"]
            and result["base_qty"] == 1
            and result["entry_buy"] == 101
            and result["exit_buy"] == 98.5
        )

    asyncio.run(go())


@pytest.mark.parametrize("unknown", [None, 0, "", [], {}])
def test_unknown_order_evidence_is_not_false_by_coercion(unknown):
    from app.micro_live_evidence import evaluate

    assert not evaluate(True, True, True, True, True, True, True, unknown, True).ready


def test_disabled_funding_cycle_still_watches_open_paper_without_discovery(tmp_path):
    from app.funding_cycle_service import CycleService

    async def go():
        e, f = await engine(tmp_path)
        await e.cycle([CANDIDATE])

        class Discovery:
            async def scan(self, symbol):
                raise AssertionError("disabled discovery was called")

        cycle = CycleService(Discovery(), [SYMBOL], paper=e)
        cycle.entry_enabled = False
        f.now = 1061
        assert await cycle.cycle() == []
        assert e.positions["1"]["status"] == "EXIT_ACCOUNTING_PENDING"
        f.now = 1100
        f.history_valid = True
        await cycle.cycle()
        assert not e.positions

    asyncio.run(go())
