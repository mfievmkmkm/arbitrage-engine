import asyncio
import json
import math
import time
from types import SimpleNamespace as NS
import pytest
from test_native_order_plan import client, SYMBOL
from app.db import Diary
from app.live_trade_store import Store
from app.live_monitor_store import Store as MonitorStore
from app.runtime_store import RuntimeStore
from app.live_entry_dispatch import Coordinator
from app.live_acceptance import accepted, CHECKS, VENUE_CHECKS
from app.private_adapter import PrivatePosition
from app.quote_order_evidence import validate


async def setup(tmp_path):
    diary = Diary(str(tmp_path / "a.db"))
    await diary.init()
    durable = Store(diary.path)
    await durable.init()
    await MonitorStore(diary.path).init()
    positions = {v: [] for v in ("a", "b")}
    sent = []
    clients = {v: client() for v in positions}
    for venue, c in clients.items():
        c.has.update(fetchPositionMode=True, fetchTradingFee=True)

        async def mode(symbol):
            return {"hedged": False}

        async def fees(symbol):
            return dict(symbol=symbol, maker=0.0002, taker=0.0005)

        async def books(symbol, limit=None, venue=venue):
            p = 100 if venue == "a" else 105
            return dict(
                symbol=symbol,
                timestamp=time.time() * 1000,
                bids=[[p - 0.01, 1000]],
                asks=[[p, 1000]],
            )

        async def create(symbol, kind, side, qty, price, params, venue=venue):
            sent.append((venue, kind, side, qty, price, params))
            actual_price = (
                price if price is not None else (99.99 if venue == "a" else 105)
            )
            positions[venue] = (
                []
                if params.get("reduceOnly")
                else [
                    PrivatePosition(
                        venue,
                        symbol,
                        "long" if side == "buy" else "short",
                        qty * 0.01,
                        actual_price,
                        qty,
                        0.01,
                    )
                ]
            )
            return dict(
                id=venue + str(len(sent)),
                symbol=symbol,
                status="closed",
                amount=qty,
                filled=qty,
                average=actual_price,
                fee=dict(cost=qty * 0.01 * actual_price * 0.0005, currency="USDT"),
            )

        c.fetch_position_mode = mode
        c.fetch_trading_fee = fees
        c.fetch_order_book = books
        c.create_order = create

    async def snapshots():
        now = time.time()
        return {
            v: dict(
                health=NS(ok=True),
                snapshot_started_at=now,
                fetched_at=now,
                positions=positions[v],
                orders=[],
                balance=NS(free=50, venue=v, currency="USDT"),
            )
            for v in positions
        }

    class Funding:
        async def pair_carry_pct(self, *args):
            return 0, True, "VERIFIED_MODEL_CALENDAR"

    runtime = RuntimeStore(str(tmp_path / "runtime.json"))
    c = Coordinator(
        durable,
        runtime,
        diary,
        clients,
        clients,
        snapshots,
        Funding(),
        lambda *args: True,
    )
    return c, clients, positions, sent


def op():
    return dict(symbol=SYMBOL, buy="a", sell="b")


def test_scanner_entry_uses_fresh_native_ioc_proofs_and_actual_fills(tmp_path):
    async def go():
        c, clients, positions, sent = await setup(tmp_path)
        result = await c.process([op()])
        assert result["opened"] and result["phase"] == "OPEN"
        assert len(sent) == 2 and all(
            x[1] == "limit" and x[-1]["timeInForce"] == "IOC" for x in sent
        )
        t = c.runtime.load()[0]
        assert t.base_qty == pytest.approx(0.04) and t.entry_fees > 0
        assert (await c.durable.get(t.trade_id))["phase"] == "OPEN"
        intents = await c.diary.order_intents()
        assert len(intents) == 2
        for iid in intents:
            proof = await c.diary.order_request_evidence(iid)
            assert proof is not None
        assert (await c.process([op()]))["status"] == "ENTRY_DURABLE_CAPACITY"
        assert len(sent) == 2

    asyncio.run(go())


@pytest.mark.parametrize(
    "problem,reason",
    [
        ("authority", "AUTHORITY"),
        ("margin", "MARGIN"),
        ("mode", "ONE_WAY"),
        ("fee", "FEE"),
        ("funding", "FUNDING"),
        ("stale", "PRIVATE_STALE"),
        ("position", "NOT_FLAT"),
        ("units", "UNITS"),
        ("book", "SYMBOL"),
        ("notional", "NOTIONAL"),
        ("daily", "DAILY"),
    ],
)
def test_failed_preflight_never_reserves_or_sends(tmp_path, problem, reason):
    async def go():
        c, clients, positions, sent = await setup(tmp_path)
        if problem == "authority":
            c.authority = lambda *args: False
        if problem == "notional":
            c.notional = 6
        if problem == "margin":
            original = c.snapshots

            async def snap():
                s = await original()
                s["a"]["balance"].free = 0
                return s

            c.snapshots = snap
        if problem == "stale":
            original = c.snapshots

            async def snap():
                s = await original()
                s["a"]["snapshot_started_at"] -= 20
                return s

            c.snapshots = snap
        if problem == "position":
            positions["a"] = [PrivatePosition("a", SYMBOL, "long", 0.1)]
        if problem == "mode":

            async def mode(*args):
                return {"hedged": True}

            clients["a"].fetch_position_mode = mode
        if problem == "fee":

            async def fee(*args):
                return dict(symbol=SYMBOL, maker=0, taker=float("nan"))

            clients["a"].fetch_trading_fee = fee
        if problem == "funding":

            async def carry(*args):
                return 0, False, "UNKNOWN"

            c.funding.pair_carry_pct = carry
        if problem == "book":

            async def book(*args, **kwargs):
                return dict(symbol="WRONG", bids=[[99, 100]], asks=[[100, 100]])

            clients["a"].fetch_order_book = book
        if problem == "units":
            import copy

            public = client(0.001)
            public.fetch_order_book = clients["a"].fetch_order_book
            c.public = {**c.public, "a": public}
        if problem == "daily":
            import aiosqlite

            async with aiosqlite.connect(c.durable.path) as d:
                await d.execute(
                    "INSERT INTO live_results VALUES('old',?,0,0,0,-2,'TEST','{}')",
                    (time.time(),),
                )
                await d.commit()
        result = await c.process([op()])
        assert reason in result["status"] and not sent and not await c.durable.active()

    asyncio.run(go())


def test_atomic_entry_capacity_blocks_two_processes(tmp_path):
    async def go():
        c, _, _, _ = await setup(tmp_path)
        meta = dict(
            symbol=SYMBOL,
            long_venue="a",
            short_venue="b",
            planned_long=1,
            planned_short=1,
        )
        second = Store(c.durable.path)
        assert (
            sum(
                await asyncio.gather(
                    c.durable.reserve_entry("one", **meta),
                    second.reserve_entry("two", **meta),
                )
            )
            == 1
        )

    asyncio.run(go())


def acceptance(now):
    return dict(
        version=1,
        evidence_id="review-1",
        verified_at=now - 1,
        expires_at=now + 100,
        checks={k: True for k in CHECKS},
        venues={v: {k: True for k in VENUE_CHECKS} for v in ("a", "b")},
    )


@pytest.mark.parametrize(
    "problem",
    [
        "missing",
        "expired",
        "future",
        "long",
        "check",
        "venue",
        "bool_time",
        "text_true",
        "id",
    ],
)
def test_acceptance_is_explicit_scoped_and_expiring(tmp_path, problem):
    p = tmp_path / "acceptance.json"
    now = time.time()
    d = acceptance(now)
    if problem == "missing":
        assert not accepted(p, ("a", "b"), now)
        return
    if problem == "expired":
        d["expires_at"] = now - 1
    if problem == "future":
        d["verified_at"] = now + 1
    if problem == "long":
        d["expires_at"] = now + 90000
    if problem == "check":
        d["checks"]["oos"] = False
    if problem == "venue":
        d["venues"]["b"]["reduce_only"] = False
    if problem == "bool_time":
        d["verified_at"] = True
    if problem == "text_true":
        d["checks"]["ci"] = "true"
    if problem == "id":
        d["evidence_id"] = ""
    p.write_text(json.dumps(d))
    assert not accepted(p, ("a", "b"), now)


def test_acceptance_complete_record_can_authorize_after_final_checks(tmp_path):
    p = tmp_path / "a.json"
    now = time.time()
    p.write_text(json.dumps(acceptance(now)))
    assert accepted(p, ("a", "b"), now) and not accepted(p, ("a", "unknown"), now)


def test_forty_dollar_budget_and_realized_loss_scale_quantity_instead_of_disabling_all_entries(
    tmp_path,
):
    async def go():
        c, _, _, sent = await setup(tmp_path)
        c.bankroll = 40
        result = await c.process([op()])
        assert result["opened"] and max(x[3] * 0.01 * x[4] for x in sent) <= 4

    asyncio.run(go())


def test_acceptance_rejects_empty_scope_and_boolean_version(tmp_path):
    p = tmp_path / "a.json"
    now = time.time()
    d = acceptance(now)
    p.write_text(json.dumps(d))
    assert not accepted(p, (), now) and not accepted(p, ("a", "a"), now)
    d["version"] = True
    p.write_text(json.dumps(d))
    assert not accepted(p, ("a", "b"), now)


def test_fill_outside_ioc_limit_remains_unknown_until_reconciliation(tmp_path):
    async def go():
        c, clients, _, sent = await setup(tmp_path)
        original = clients["a"].create_order

        async def bad(*args, **kwargs):
            result = await original(*args, **kwargs)
            result["average"] += 1
            return result

        clients["a"].create_order = bad
        result = await c.process([op()])
        assert not result["opened"] and "SUBMIT_UNKNOWN_RECONCILE" in result["status"]
        assert len(sent) == 2 and await c.durable.active()
        assert not c.runtime.load()

    asyncio.run(go())


def test_protective_flat_cycle_can_be_accounted_without_committed_runtime(tmp_path):
    from test_live_monitor_integration import setup as monitor_setup, entries, fill

    async def go():
        m, _ = await monitor_setup(tmp_path, flat=True)
        await entries(m)
        await fill(m, "exit-l", "a", "sell", 4, 102, True, 0.2)
        await fill(m, "exit-s", "b", "buy", 2, 103, True, 0.2)
        result = await m.cycle()
        assert len(result["closed"]) == 1 and not (await m.cycle())["closed"]

    asyncio.run(go())


def test_private_mismatch_uses_fresh_protective_quotes_and_clears_exposure(tmp_path):
    async def go():
        c, clients, positions, sent = await setup(tmp_path)
        original = c.snapshots

        async def mismatch():
            from dataclasses import replace

            if positions["a"] and positions["b"]:
                positions["a"] = [replace(positions["a"][0], qty=0.03, contracts=3)]
            return await original()

        c.snapshots = mismatch
        result = await c.process([op()])
        assert not result["opened"] and "PRIVATE_POSITION_MISMATCH" in result["status"]
        assert len(sent) == 4 and all(x[-1].get("reduceOnly") for x in sent[2:])
        assert not positions["a"] and not positions["b"] and not c.runtime.load()

    asyncio.run(go())


@pytest.mark.parametrize(
    "field,value",
    [("checks", []), ("venues", []), ("venues", {"a": None}), ("root", [])],
)
def test_malformed_acceptance_record_fails_closed(tmp_path, field, value):
    p = tmp_path / "a.json"
    now = time.time()
    d = acceptance(now)
    if field == "root":
        d = value
    else:
        d[field] = value
    p.write_text(json.dumps(d))
    assert not accepted(p, ("a", "b"), now)
