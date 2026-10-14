import asyncio
import copy
import json
import time
from dataclasses import replace
from types import SimpleNamespace as NS
import pytest
from test_live_monitor_integration import setup, entries, trade
from app.live_exit_dispatch import Coordinator
from app.live_trade_store import Store
from app.safe_executor import SafeExecutor
from app.mock_executor import MockExecutor
from app.close_recovery import plan
from app.exchange_executor import SubmitResult


class Quotes:
    clock = staticmethod(time.time)

    async def quote(self, venue, request, size):
        now = time.time()
        evidence = dict(
            source="PUBLIC_REST_RECOVERY_V1",
            venue=venue,
            symbol=request.symbol,
            side=request.side,
            contracts=request.qty,
            reduce_only=True,
            contract_size=size,
            base_qty=request.qty * size,
            book_ts=now,
            started_at=now,
            received_at=now,
            bids=[[102, 100]],
            asks=[[103, 100]],
        )
        return replace(
            request,
            reference_price=102 if request.side == "sell" else 103,
            market_evidence=evidence,
        )


async def ready(tmp_path):
    m, snap = await setup(tmp_path)
    await entries(m)
    summary = await m.cycle()
    summary["trades"][0]["exit_signal"] = "TARGET_CAPTURE"
    executors = {
        v: SafeExecutor(
            v, MockExecutor(price=p), m.diary, lambda: False, exit_gate=lambda: True
        )
        for v, p in (("a", 102), ("b", 103))
    }
    c = Coordinator(
        m.durable, executors, Quotes(), lambda t: True, m.stop, clock=lambda: 1000
    )
    return m, snap, summary, c


def test_dispatch_closes_both_legs_but_only_monitor_finalizes(tmp_path):
    async def go():
        m, snap, summary, c = await ready(tmp_path)
        result = await c.process(summary)
        assert result[0].attempted and result[0].status == "EXIT_FILLS_PENDING_PRIVATE"
        assert (await m.durable.get("t"))["phase"] == "EXIT_SUBMITTING"
        assert (await m.store.totals())["closed"] == 0
        assert all(
            x["reduce_only"]
            for k, x in (await m.diary.order_intents()).items()
            if "exit" in k
        )
        assert (await c.process(summary))[0].status == "EXIT_ALREADY_CLAIMED_OR_CHANGED"
        for row in snap.values():
            row["positions"] = []
        closed = await m.cycle()
        assert len(closed["closed"]) == 1 and not (await m.cycle())["closed"]

    asyncio.run(go())


@pytest.mark.parametrize(
    "change,reason",
    [
        ("authority", "AUTHORITY"),
        ("private", "RECONCILIATION"),
        ("unknown", "RECONCILIATION"),
        ("position", "POSITION"),
        ("cost", "COSTS"),
        ("stale", "STALE"),
        ("future", "STALE"),
        ("scope", "SCOPE"),
        ("runtime", "RUNTIME"),
        ("executor", "EXECUTOR"),
        ("market", "AUTHORITY"),
    ],
)
def test_no_write_without_coherent_authority(tmp_path, change, reason):
    async def go():
        m, _, s, c = await ready(tmp_path)
        mark = s["trades"][0]
        if change == "authority":
            c.authority = lambda t: False
        if change == "private":
            s["reconciled"] = False
        if change == "unknown":
            s["unknown_orders"] = 1
        if change == "position":
            mark["private_verified"] = False
        if change == "cost":
            mark["costs_verified"] = False
        if change == "stale":
            mark["market_ts"] = 998
        if change == "future":
            s["ts"] = 1001
        if change == "scope":
            mark["symbol"] = "OTHER"
        if change == "runtime":
            s["runtime_trades"] = []
        if change == "executor":
            c.executors = {}
        if change == "market":
            c.market = None
        result = await c.process(s)
        assert reason in result[0].status and not result[0].attempted
        assert (await m.durable.get("t"))["phase"] == "OPEN"
        assert len(await m.diary.order_intents()) == 2

    asyncio.run(go())


def test_cross_process_exit_claim_and_stale_mark_cannot_reopen(tmp_path):
    async def go():
        m, _, s, c = await ready(tmp_path)
        other = Store(m.durable.path)
        t = trade()
        claims = await asyncio.gather(
            m.durable.claim_exit(t, "TIME_STOP"), other.claim_exit(t, "TIME_STOP")
        )
        assert sum(claims) == 1
        assert not await other.mark_open("t", last_exit_signal="HOLD")
        assert (await other.get("t"))["phase"] == "EXIT_SUBMITTING"

    asyncio.run(go())


def test_changed_position_or_hold_cannot_be_claimed(tmp_path):
    async def go():
        m, _, s, c = await ready(tmp_path)
        t = trade()
        t.base_qty = 1
        assert not await m.durable.claim_exit(t, "TIME_STOP")
        await m.durable.phase("t", "OPEN", exit_hold_reason="UNCERTAIN")
        assert not await m.durable.claim_exit(trade(), "TIME_STOP")

    asyncio.run(go())


def test_authority_loss_during_reservation_does_not_send_or_retry(tmp_path):
    async def go():
        m, _, s, c = await ready(tmp_path)
        allowed = [True]
        c.authority = lambda t: allowed[0]
        original = c.durable.claim_exit

        async def claim(*args):
            result = await original(*args)
            allowed[0] = False
            return result

        c.durable.claim_exit = claim
        result = await c.process(s)
        assert not result[0].attempted and "AUTHORITY" in result[0].status
        assert len(await m.diary.order_intents()) == 2
        assert (await m.durable.get("t"))["phase"] == "EXIT_SUBMITTING"
        assert m.stop.stopped

    asyncio.run(go())


@pytest.mark.parametrize(
    "error", [RuntimeError("unexpected"), asyncio.CancelledError()]
)
def test_interruption_preserves_reservation_and_stops(tmp_path, error):
    async def go():
        m, _, s, c = await ready(tmp_path)

        async def fail(*args, **kwargs):
            raise error

        c.runner = fail
        if isinstance(error, asyncio.CancelledError):
            with pytest.raises(asyncio.CancelledError):
                await c.process(s)
        else:
            assert (await c.process(s))[0].status == "EXIT_DISPATCH_UNKNOWN"
        row = await m.durable.get("t")
        assert (
            row["phase"] == "EXIT_SUBMITTING"
            and json.loads(row["payload"])["exit_hold_reason"]
        )
        assert m.stop.stopped

    asyncio.run(go())


def test_ambiguous_submit_is_not_retried(tmp_path):
    async def go():
        m, _, s, c = await ready(tmp_path)

        async def uncertain(*args, **kwargs):
            return NS(execution=None, status="EXIT_UNCERTAIN:UNKNOWN")

        c.runner = uncertain
        assert "UNCERTAIN" in (await c.process(s))[0].status
        assert (await c.process(s))[0].status == "EXIT_ALREADY_CLAIMED_OR_CHANGED"

    asyncio.run(go())


def test_tiny_unfilled_order_is_not_treated_as_flat():
    t = NS(long_contracts=1e-12, short_contracts=1e-12, long_venue="a", short_venue="b")
    x = NS(
        long_result=SubmitResult("l", "canceled", 0),
        short_result=SubmitResult("s", "filled", 1e-12),
    )
    r = plan(t, x)
    assert r.required and r.contracts == 1e-12 and r.venue == "a"


def test_partial_exit_recovers_only_the_known_remaining_leg(tmp_path):
    async def go():
        m, snap, s, c = await ready(tmp_path)

        class FirstPartial(MockExecutor):
            async def submit(self, request):
                self.fill_ratio = 0.5 if self.seq == 0 else 1
                return await super().submit(request)

        c.executors["a"] = SafeExecutor(
            "a", FirstPartial(price=102), m.diary, lambda: False, exit_gate=lambda: True
        )
        result = await c.process(s)
        assert result[0].status == "EXIT_FILLS_PENDING_PRIVATE"
        intents = await m.diary.order_intents()
        recovery = [
            x
            for k, x in intents.items()
            if x["reduce_only"] and k not in ("t:exit-long", "t:exit-short")
        ]
        assert (
            len(recovery) == 1
            and recovery[0]["venue"] == "a"
            and recovery[0]["qty"] == 2
        )
        for row in snap.values():
            row["positions"] = []
        assert len((await m.cycle())["closed"]) == 1

    asyncio.run(go())


def test_both_legs_partial_preserves_state_without_inventing_flatness(tmp_path):
    async def go():
        m, _, s, c = await ready(tmp_path)
        c.executors = {
            v: SafeExecutor(
                v,
                MockExecutor(fill_ratio=0.5, price=p),
                m.diary,
                lambda: False,
                exit_gate=lambda: True,
            )
            for v, p in (("a", 102), ("b", 103))
        }
        result = await c.process(s)
        assert result[0].status == "EXIT_RECOVERY_REQUIRED:BOTH_LEGS_RESIDUAL_RECONCILE"
        assert len(await m.diary.order_intents()) == 4 and m.stop.stopped
        assert (await m.store.totals())["closed"] == 0

    asyncio.run(go())
