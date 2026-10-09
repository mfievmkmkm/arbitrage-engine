import asyncio
import json
import math
from types import SimpleNamespace as NS
from dataclasses import replace
import pytest
from tests.test_spot_live_units import client, SS
from tests.test_spot_future_live_session import Clock
from tests.test_live_entry_dispatch import (
    setup as derivative_setup,
    op as derivative_op,
)
from app.db import Diary
from app.live_trade_store import Store
from app.live_monitor_store import Store as Results
from app.spot_spot_live import Session
from app.live_spot_spot_dispatch import Coordinator, CashObserver
from app.live_funding_dispatch import Coordinator as FundingCoordinator
from app.private_funding_reader import POLICY
from app.live_acceptance import accepted, CHECKS, VENUE_CHECKS, SPOT_CHECKS


async def setup(tmp_path, base_fee=True):
    clock = Clock()
    states = {v: dict(X=2.0, USDT=50.0) for v in ("a", "b")}
    clients = {v: client() for v in states}
    for v, c in clients.items():
        c.has.update(fetchTradingFee=True, fetchOrders=True, fetchOrder=True)
        c.buy_price = 100.0 if v == "a" else 110.01
        c.sell_price = 99.99 if v == "a" else 110.0
        c.ratio = 1
        c.unknown = False
        c.sent = []
        c.orders = {}
        c.base_fee = base_fee

        async def fee(symbol):
            return dict(symbol=symbol, maker=0.001, taker=0.001)

        async def book(symbol, limit=20, c=c):
            return dict(
                symbol=symbol,
                timestamp=int(clock() * 1000),
                bids=[[c.sell_price, 1000]],
                asks=[[c.buy_price, 1000]],
            )

        async def balance(v=v):
            return {k: dict(total=q, free=q, used=0) for k, q in states[v].items()}

        async def open_orders(*a):
            return []

        async def create(symbol, kind, side, qty, price, params, v=v, c=c):
            c.sent.append((side, qty, price, params))
            q = qty * c.ratio
            fee = q * 0.001 if c.base_fee else q * price * 0.001
            states[v]["X"] += (q if side == "buy" else -q) - (fee if c.base_fee else 0)
            states[v]["USDT"] += (-q * price if side == "buy" else q * price) - (
                0 if c.base_fee else fee
            )
            row = dict(
                id=str(len(c.sent)),
                symbol=symbol,
                side=side,
                amount=qty,
                filled=q,
                average=price if q else None,
                status="canceled",
                fee=dict(cost=fee, currency="X" if c.base_fee else "USDT"),
                clientOrderId=params["clientOrderId"],
            )
            c.orders[row["id"]] = row
            if c.unknown:
                raise TimeoutError("ACK lost")
            return row

        async def order(oid, symbol, c=c):
            return dict(c.orders[oid])

        async def history(symbol, c=c):
            return list(c.orders.values())

        c.fetch_trading_fee = fee
        c.fetch_order_book = book
        c.fetch_balance = balance
        c.fetch_open_orders = open_orders
        c.create_order = create
        c.fetch_order = order
        c.fetch_orders = history
    d = Diary(str(tmp_path / "d.db"))
    await d.init()
    durable = Store(d.path)
    await durable.init()
    await Results(d.path).init()
    stops = []
    s = Session(
        durable, d, clients, lambda v: True, lambda v: True, stops.append, clock=clock
    )
    return s, clients, states, clock, stops


def offer():
    return dict(symbol=SS, buy="a", sell="b", net=8)


@pytest.mark.parametrize("base_fee", [True, False])
def test_spot_spot_terminal_private_round_trip_and_idempotent_result(
    tmp_path, base_fee
):
    async def go():
        s, cs, states, clock, stops = await setup(tmp_path, base_fee)
        p = await s.preview(offer())
        assert p["status"] == "DATA_CHECKED"
        assert not any(c.sent for c in cs.values())
        r = await s.enter(offer(), "ss-abc")
        assert r["status"] == "OPEN", r
        assert len(cs["a"].sent) == len(cs["b"].sent) == 1
        assert states["a"]["X"] > 2 and states["b"]["X"] < 2
        cs["b"].buy_price = 100
        cs["b"].sell_price = 99.99
        assert (await s.close("ss-abc"))["status"] == "ACCOUNTING_PENDING"
        r = await s.finalize("ss-abc")
        assert r["status"] == "CLOSED", r
        assert r["result"]["net"] > 0.35
        assert r["result"]["net"] == pytest.approx(
            r["result"]["gross"] - r["result"]["fees"]
        )
        assert (await s.finalize("ss-abc"))["status"] == "RECONCILE_REQUIRED"
        assert not await s.store.active()
        assert (await Results(s.store.path).totals())["closed"] == 1
        assert not stops

    asyncio.run(go())


@pytest.mark.parametrize(
    "problem,reason",
    [
        ("inventory", "PREPOSITIONED"),
        ("quote", "BUFFER"),
        ("authority", "AUTHORITY"),
        ("stale", "STALE"),
        ("fee", "FEE"),
    ],
)
def test_spot_spot_admission_blocks_before_reserve_or_send(tmp_path, problem, reason):
    async def go():
        s, cs, states, clock, _ = await setup(tmp_path)
        if problem == "inventory":
            states["b"]["X"] = 0
        if problem == "quote":
            states["b"]["USDT"] = 0
        if problem == "authority":
            s.entry_authority = lambda v: False
        if problem == "fee":

            async def bad(symbol):
                return dict(symbol="OTHER", taker=0.001)

            cs["b"].fetch_trading_fee = bad
        if problem == "stale":

            async def stale(symbol, limit=20):
                return dict(
                    symbol=symbol,
                    timestamp=int((clock() - 5) * 1000),
                    bids=[[110, 100]],
                    asks=[[110.01, 100]],
                )

            cs["b"].fetch_order_book = stale
        r = await s.enter(offer(), "ss-no")
        assert r["status"] == "BLOCKED" and reason in r["reason"], r
        assert not any(c.sent for c in cs.values())
        assert await s.store.get("ss-no") is None

    asyncio.run(go())


def test_spot_spot_unknown_ack_holds_restart_reads_and_explicit_recovery(tmp_path):
    async def go():
        s, cs, states, clock, stops = await setup(tmp_path)
        cs["a"].unknown = True
        r = await s.enter(offer(), "ss-unknown")
        assert r["status"] == "HOLD"
        assert len(cs["a"].sent) == 1 and not cs["b"].sent and stops
        cs["a"].unknown = False
        # Restart/reconcile may find the terminal order but never continues entry.
        restarted = Session(
            s.store, s.diary, cs, lambda v: False, lambda v: True, clock=clock
        )
        n = sum(len(c.sent) for c in cs.values())
        obs = await restarted.reconcile("ss-unknown")
        assert obs["status"] == "PRIVATE_VERIFIED", obs
        assert sum(len(c.sent) for c in cs.values()) == n
        r = await restarted.recover("ss-unknown")
        assert r["status"] == "ACCOUNTING_PENDING", r
        assert not cs["b"].sent
        assert (await restarted.finalize("ss-unknown"))["status"] == "CLOSED"

    asyncio.run(go())


def test_spot_spot_private_external_balance_change_prohibits_close(tmp_path):
    async def go():
        s, cs, states, _, _ = await setup(tmp_path)
        assert (await s.enter(offer(), "ss-change"))["status"] == "OPEN"
        states["a"]["USDT"] += 1
        n = sum(len(c.sent) for c in cs.values())
        assert (await s.close("ss-change"))["status"] == "HOLD"
        assert sum(len(c.sent) for c in cs.values()) == n

    asyncio.run(go())


def test_spot_spot_monitor_dynamic_exit_and_shared_capacity(tmp_path):
    async def go():
        s, cs, states, clock, _ = await setup(tmp_path)
        c = Coordinator(s)
        assert (await c.process_rows([offer()]))["status"] == "OPEN"
        assert (await c.process_rows([offer()]))["status"] == "GLOBAL_LIVE_CAPACITY"
        row = (await s.store.active())[0]
        cs["b"].buy_price = 100
        cs["b"].sell_price = 99.99
        info = await c.observe(row)
        assert (
            info["private_verified"] and info["exit_signal"] == "TARGET_CAPTURE"
        ), info
        summary = dict(
            ts=clock(),
            private_verified=True,
            reconciled=True,
            unknown_orders=0,
            trades=[info],
        )
        assert await c.process(summary) == []
        row = (await s.store.active())[0]
        assert row["phase"] == "SS_ACCOUNTING_PENDING"
        summary["trades"] = [await c.observe(row)]
        assert len(await c.process(summary)) == 1
        assert await c.process(summary) == []

    asyncio.run(go())


def test_spot_spot_partial_restoration_needs_bounded_explicit_recovery(tmp_path):
    async def go():
        s, cs, states, _, stops = await setup(tmp_path)
        assert (await s.enter(offer(), "ss-partial"))["status"] == "OPEN"
        cs["b"].ratio = 0.25
        assert (await s.close("ss-partial"))["status"] == "HOLD"
        assert len(cs["a"].sent) == 1  # no sale until restoration succeeded
        cs["b"].ratio = 1
        assert (await s.recover("ss-partial"))["status"] == "ACCOUNTING_PENDING"
        assert len(cs["b"].sent) == 3 and len(cs["a"].sent) == 2
        ids = [o[3]["clientOrderId"] for o in cs["b"].sent]
        assert len(ids) == len(set(ids))
        assert (await s.finalize("ss-partial"))["status"] == "CLOSED"

    asyncio.run(go())


async def funding_setup(tmp_path, monkeypatch):
    c, clients, positions, sent = await derivative_setup(tmp_path)
    for v in clients:
        monkeypatch.setitem(POLICY, v, ("income", 1))

    async def get(v, symbol):
        return NS(
            exchange=v,
            symbol=symbol,
            rate=0.0001 if v == "a" else 0.001,
            interval_hours=8,
            next_ts=(c.clock() + 3600) * 1000,
        )

    c.funding.get = get
    f = FundingCoordinator(
        c.durable,
        c.runtime,
        c.diary,
        c.public,
        c.private,
        c.snapshots,
        c.funding,
        c.authority,
        max_seconds=28800,
    )
    return f, clients, positions, sent


def test_funding_live_saves_strategy_calendar_before_execution(tmp_path, monkeypatch):
    async def go():
        c, clients, _, sent = await funding_setup(tmp_path, monkeypatch)
        r = await c.process_rows(
            [
                dict(
                    symbol=derivative_op()["symbol"],
                    long_venue="a",
                    short_venue="b",
                    projected_net_pct=1,
                )
            ]
        )
        assert r["opened"], r
        row = (await c.durable.active())[0]
        meta = json.loads(row["payload"])
        assert (
            meta["strategy"] == "funding_arb"
            and meta["funding_plan"]["hold_seconds"] == 28800
        )
        assert meta["funding_plan"]["forecast_carry_pct"] == pytest.approx(0.09)
        assert len(sent) == 2
        assert meta["runtime_trade"]["funding"] == 0

    asyncio.run(go())


@pytest.mark.parametrize("fault", ["scope", "rate", "near", "negative", "interval"])
def test_funding_live_forecast_cannot_bypass_entry_truth(tmp_path, monkeypatch, fault):
    async def go():
        c, clients, _, sent = await funding_setup(tmp_path, monkeypatch)
        original = c.funding.get

        async def bad(v, symbol):
            x = await original(v, symbol)
            if fault == "scope":
                x.symbol = "OTHER"
            if fault == "rate":
                x.rate = float("nan")
            if fault == "near":
                x.next_ts = (c.clock() + 20) * 1000
            if fault == "negative":
                x.rate = 0
            if fault == "interval":
                x.interval_hours = 0
            return x

        c.funding.get = bad
        r = await c.process([derivative_op()])
        assert not r.get("opened") and not sent

    asyncio.run(go())


@pytest.mark.parametrize(
    "strategy,extra",
    [
        ("spot_spot", SPOT_CHECKS + ("inventory_restoration",)),
        (
            "funding_arb",
            ("settlement_calendar", "private_income", "holding_exit", "spread_stop"),
        ),
    ],
)
def test_expiring_separate_cash_and_funding_acceptance(tmp_path, strategy, extra):
    path = tmp_path / "accept.json"
    d = dict(
        version=1,
        evidence_id="scope",
        verified_at=100,
        expires_at=200,
        checks=dict.fromkeys(CHECKS, True),
        venues={v: dict.fromkeys(VENUE_CHECKS, True) for v in ("a", "b")},
    )
    path.write_text(json.dumps(d))
    assert not accepted(path, ("a", "b"), 150, strategy)
    d["strategies"] = {
        strategy: dict(venues={v: dict.fromkeys(extra, True) for v in ("a", "b")})
    }
    path.write_text(json.dumps(d))
    assert accepted(path, ("a", "b"), 150, strategy)
    for name in extra:
        d["strategies"][strategy]["venues"]["b"][name] = False
        path.write_text(json.dumps(d))
        assert not accepted(path, ("a", "b"), 150, strategy)
        d["strategies"][strategy]["venues"]["b"][name] = True
    path.write_text(json.dumps(d))
    assert not accepted(path, ("a", "b"), 201, strategy)


def test_common_monitor_spot_spot_owns_cash_without_derivative_reconstruction(tmp_path):
    from app.live_monitor import Monitor
    from app.runtime_store import RuntimeStore
    from app.live_supervisor import LiveSupervisor
    from app.persistent_stop import Stop

    async def go():
        s, cs, states, clock, _ = await setup(tmp_path)
        assert (await s.enter(offer(), "ss-common"))["status"] == "OPEN"
        coordinator = Coordinator(s)

        async def source():
            return {
                v: dict(
                    health=NS(ok=True),
                    snapshot_started_at=clock(),
                    fetched_at=clock(),
                    positions=[],
                    orders=[],
                )
                for v in cs
            }

        observer = CashObserver(
            NS(inventory=lambda: asyncio.sleep(0, result=[])), coordinator
        )
        monitor = Monitor(
            s.store,
            RuntimeStore(str(tmp_path / "runtime.json")),
            s.diary,
            source,
            {
                v
                + ":spot": __import__(
                    "app.spot_executor", fromlist=["SpotOrderReader"]
                ).SpotOrderReader(v + ":spot", c)
                for v, c in cs.items()
            },
            LiveSupervisor(),
            Stop(tmp_path / "stop.json"),
            clock=clock,
            cash_observer=observer,
        )
        await monitor.init()
        summary = await monitor.cycle()
        assert summary["private_verified"] and summary["reconciled"], summary
        assert summary["runtime_trades"] == [] and len(summary["trades"]) == 1
        assert summary["trades"][0]["strategy"] == "spot_spot"
        assert not any(
            x["code"] in ("UNMANAGED_POSITION", "RUNTIME_PAYLOAD_INVALID")
            for x in summary["incidents"]
        )

    asyncio.run(go())


def test_funding_monitor_uses_dedicated_horizon_and_price_stop_without_forecast_income(
    tmp_path,
):
    from tests.test_live_monitor_integration import setup as monitor_setup, entries

    async def go():
        m, _ = await monitor_setup(tmp_path, state="OPEN", canonical=True)
        await entries(m)
        m.max_seconds = 60
        await m.durable.phase(
            "t", "OPEN", strategy="funding_arb", funding_plan=dict(hold_seconds=28800)
        )
        m.funding.verified = False
        r = await m.cycle()
        assert r["trades"][0]["strategy"] == "funding_arb"
        assert r["trades"][0]["exit_signal"] == "HOLD"
        m.stop_net = -0.5

        async def adverse(t):
            return dict(
                ok=True,
                long_exit=90,
                short_exit=110,
                exit_fee=0.2,
                fees_verified=True,
                ts=1000,
                spread=1,
            )

        m.market.mark = adverse
        r = await m.cycle()
        assert r["trades"][0]["exit_signal"] == "NET_STOP"
        assert not r["trades"][0]["costs_verified"]

    asyncio.run(go())


@pytest.mark.parametrize("recent", [False, True])
def test_private_funding_mark_requires_mature_income_and_calendar_gap_proof(recent):
    from app.private_funding_reader import PairReader, Evidence

    async def go():
        class Income:
            maturity = 30

            async def collect(self, symbol, start, until):
                return Evidence(True, 0.2, (), "VERIFIED", until)

        async def get(v, symbol):
            return NS(
                exchange=v,
                symbol=symbol,
                next_ts=(1000 + 28780 if recent else 2000),
                interval_hours=8,
            )

        reader = PairReader(
            {"a": Income(), "b": Income()}, NS(get=get), clock=lambda: 1000
        )
        r = await reader.mark(
            NS(long_venue="a", short_venue="b", symbol="X", opened_at=900), 1000
        )
        assert r.verified is (not recent) and r.amount == pytest.approx(0.4)
        assert r.covered_until == (970 if recent else 1000)

    asyncio.run(go())


def test_fill_cash_decisions_do_not_claim_held_inventory_flat_and_ui_scope(tmp_path):
    from app.tg_remaining_live import menu, render
    from app.live_monitor_view import positions

    async def go():
        s, cs, states, clock, _ = await setup(tmp_path)
        c = Coordinator(s)
        assert (await s.enter(offer(), "ss-ui"))["status"] == "OPEN"
        info = await c.observe(await s.store.get("ss-ui"))
        summary = dict(trades=[info])
        buttons = menu("spot_spot", summary).inline_keyboard
        assert any(
            x.callback_data == "ss_close:ss-ui" and x.style == "danger"
            for row in buttons
            for x in row
        )
        assert "/ss_live" in positions(summary)
        assert "Изменение запаса" in render("spot_spot", summary, c)
        await s.close("ss-ui")
        r = await s.finalize("ss-ui")
        assert r["result"]["inventory_deltas"]
        row = await s.store.get("ss-ui")
        assert row["phase"] == "CLOSED_WITH_INVENTORY"

    asyncio.run(go())


def test_cash_reservation_crash_before_claim_can_be_explicitly_aborted(tmp_path):
    async def go():
        s, cs, _, clock, _ = await setup(tmp_path)
        p, buy, sell = await s.prepare(offer())
        assert await s.store.reserve_entry(
            "ss-reserved",
            strategy="spot_spot",
            symbol=p.symbol,
            long_venue="a:spot",
            short_venue="b:spot",
            planned_long=buy.qty,
            planned_short=sell.qty,
            spot_spot_plan=vars(p),
        )
        assert (await s.reconcile("ss-reserved"))["status"] == "PRIVATE_VERIFIED"
        assert (await s.recover("ss-reserved"))["status"] == "ABORTED"
        assert not await s.store.active() and not any(c.sent for c in cs.values())

    asyncio.run(go())


def test_spot_future_unclaimed_reservation_abort_uses_private_baseline(tmp_path):
    from tests.test_spot_future_live_session import setup as sf_setup

    async def go():
        s, op, spot, future, state, clock, gates, halted = await sf_setup(tmp_path)
        p = await s.admission.prepare(op)
        plan = dict(
            venue=p.venue,
            base=p.base,
            spot_symbol=p.spot_symbol,
            future_symbol=p.future_symbol,
            contract_size=p.contract_size,
            baseline_total=p.baseline_total,
            spot_fee_rate=p.spot_fee_rate,
            future_fee_rate=p.future_fee_rate,
            opened_at=clock(),
        )
        assert await s.store.reserve_entry(
            "sf-reserved",
            strategy="spot_futures",
            symbol=p.future_symbol,
            long_venue="binance:spot",
            short_venue="binance",
            planned_long=p.spot.qty,
            planned_short=p.future.qty,
            cash_plan=plan,
        )
        assert (await s.reconcile("sf-reserved"))["status"] == "PRIVATE_VERIFIED"
        assert (await s.recover("sf-reserved"))["status"] == "ABORTED"
        assert not spot.sent and not future.sent and not await s.store.active()

    asyncio.run(go())
