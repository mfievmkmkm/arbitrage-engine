import asyncio
import copy
import json
from types import SimpleNamespace as NS
import pytest
from test_live_monitor_integration import setup, entries, fill, trade, SYMBOL
from test_live_exit_dispatch import Quotes
from app.private_adapter import PrivatePosition
from app.exit_residual_evidence import evaluate
from app.live_residual_dispatch import Coordinator
from app.safe_executor import SafeExecutor
from app.mock_executor import MockExecutor


async def ready(tmp_path, ratios=(1, 1)):
    m, snap = await setup(tmp_path, state="EXIT_SUBMITTING", canonical=True)
    await entries(m)
    await fill(m, "t:exit-long", "a", "sell", 2, 102, True)
    await fill(m, "t:exit-short", "b", "buy", 1, 103, True)
    await m.durable.phase(
        "t",
        "EXIT_SUBMITTING",
        exit_dispatch_status="EXIT_RESIDUAL_PENDING_RECONCILIATION",
    )
    for v, side, q, size in (("a", "long", 2, 0.5), ("b", "short", 1, 1)):
        snap[v]["positions"] = [
            PrivatePosition(v, SYMBOL, side, q * size, 100, q, size)
        ]
    sent = []

    class Execution(MockExecutor):
        def __init__(self, venue, ratio, price):
            super().__init__(ratio, price)
            self.venue = venue
            self.seq = 10  # Distinct from all recorded entry/exit order IDs.

        async def submit(self, request):
            r = await super().submit(request)
            sent.append((self.venue, request))
            p = snap[self.venue]["positions"][0]
            remaining = p.contracts - r.filled
            snap[self.venue]["positions"] = (
                []
                if remaining == 0
                else [
                    PrivatePosition(
                        p.venue,
                        p.symbol,
                        p.side,
                        remaining * p.contract_size,
                        p.entry_price,
                        remaining,
                        p.contract_size,
                    )
                ]
            )
            return r

    executors = {
        v: SafeExecutor(
            v, Execution(v, ratio, p), m.diary, lambda: False, exit_gate=lambda: True
        )
        for v, ratio, p in zip(("a", "b"), ratios, (102, 103))
    }
    c = Coordinator(
        m.durable,
        m.diary,
        executors,
        m.snapshot_source,
        Quotes(),
        lambda t: True,
        m.stop,
        clock=lambda: 1000,
    )
    return m, snap, c, sent


def test_both_residuals_close_and_monitor_accounts_exactly_once(tmp_path):
    async def go():
        m, snap, c, sent = await ready(tmp_path)
        summary = await m.cycle()
        assert (
            summary["reconciled"]
            and summary["trades"][0]["exit_signal"] == "EXIT_RESIDUAL"
        )
        x = await c.process(summary)
        assert x[0]["status"] == "RESIDUAL_FILLS_PENDING_PRIVATE" and len(sent) == 2
        assert {v: r.qty for v, r in sent} == {"a": 2, "b": 1} and all(
            r.reduce_only for _, r in sent
        )
        assert (await m.store.totals())["closed"] == 0
        result = await m.cycle()
        assert len(result["closed"]) == 1 and not (await m.cycle())["closed"]
        assert not await c.process(result) and len(sent) == 2
        assert (await m.store.totals())["net"] == pytest.approx(7.7)

    asyncio.run(go())


@pytest.mark.parametrize(
    "problem",
    [
        "stale",
        "future",
        "health",
        "working",
        "side",
        "scope",
        "units",
        "mismatch",
        "missing_contracts",
        "unknown",
        "fee",
        "duplicate_order",
        "entry_qty",
        "entry_price",
        "entry_fee",
        "hold",
        "authority",
        "unreconciled",
    ],
)
def test_no_close_without_coherent_private_and_fill_evidence(tmp_path, problem):
    async def go():
        m, s, c, sent = await ready(tmp_path)
        summary = await m.cycle()
        p = s["a"]["positions"][0]
        from dataclasses import replace

        if problem == "stale":
            s["a"]["snapshot_started_at"] = 980
        if problem == "future":
            s["a"]["fetched_at"] = 1001
        if problem == "health":
            s["a"]["health"] = NS(ok=False)
        if problem == "working":
            s["a"]["orders"] = [NS(symbol=SYMBOL)]
        if problem == "side":
            s["a"]["positions"] = [replace(p, side="short")]
        if problem == "scope":
            s["a"]["positions"] = [replace(p, symbol="other")]
        if problem == "units":
            s["a"]["positions"] = [replace(p, contract_size=1)]
        if problem == "mismatch":
            s["a"]["positions"] = [replace(p, contracts=1, qty=0.5)]
        if problem == "missing_contracts":
            s["a"]["positions"] = [replace(p, contracts=None)]
        if problem in ("unknown", "fee", "duplicate_order"):
            from app.exchange_executor import SubmitResult

            r = SubmitResult(
                "l" if problem == "duplicate_order" else "t:exit-long",
                "open" if problem == "unknown" else "closed",
                2,
                102,
                None if problem == "fee" else 0.1,
            )
            await m.diary.update_order_intent_reconciled(
                "t:exit-long", "UNKNOWN" if problem == "unknown" else "FILLED", r
            )
        if problem.startswith("entry_"):
            t = trade()
            if problem == "entry_qty":
                t.long_contracts = 4.01
                t.base_qty = 2.005
                t.short_contracts = 2.005
            if problem == "entry_price":
                t.long_entry = 101
            if problem == "entry_fee":
                t.entry_fees = 0.5
            await m.durable.phase("t", "EXIT_SUBMITTING", runtime_trade=t.row())
        if problem == "hold":
            await m.durable.phase("t", "EXIT_SUBMITTING", exit_hold_reason="UNKNOWN")
        if problem == "authority":
            c.authority = lambda t: False
        if problem == "unreconciled":
            summary["reconciled"] = False
        await c.process(summary)
        assert not sent and len(await m.diary.order_intents()) == 4

    asyncio.run(go())


def test_claim_is_atomic_across_processes_and_never_repeats_after_restart(tmp_path):
    async def go():
        m, s, c, sent = await ready(tmp_path)
        proof = evaluate(
            trade(),
            await m.durable.get("t"),
            list((await m.diary.order_intents("t")).values()),
            s,
            1000,
        )
        other = Coordinator(
            m.durable,
            m.diary,
            c.executors,
            c.snapshots,
            c.market,
            c.authority,
            c.stop,
            clock=c.clock,
        )
        claims = await asyncio.gather(
            c.claim(trade(), proof), other.claim(trade(), proof)
        )
        assert sum(x[0] is not None for x in claims) == 1
        summary = await m.cycle()
        x = await other.process(summary)
        assert x[0]["status"] == "RESIDUAL_ALREADY_CLAIMED" and not sent
        assert (
            len(
                json.loads((await m.durable.get("t"))["payload"])[
                    "exit_residual_rounds"
                ]
            )
            == 1
        )

    asyncio.run(go())


def test_partial_progress_uses_new_ids_and_only_remaining_quantity(tmp_path):
    async def go():
        m, s, c, sent = await ready(tmp_path, (0.5, 0.5))
        summary = await m.cycle()
        assert (await c.process(summary))[0][
            "status"
        ] == "RESIDUAL_FILLS_PENDING_PRIVATE"
        summary = await m.cycle()
        for ex in c.executors.values():
            ex.inner.fill_ratio = 1
        assert (await c.process(summary))[0][
            "status"
        ] == "RESIDUAL_FILLS_PENDING_PRIVATE"
        assert {v: [r.qty for venue, r in sent if venue == v] for v in ("a", "b")} == {
            "a": [2, 1],
            "b": [1, 0.5],
        }
        assert len(set(r.client_order_id for _, r in sent)) == 4
        assert len((await m.cycle())["closed"]) == 1

    asyncio.run(go())


@pytest.mark.parametrize("kind", ["zero", "limit"])
def test_no_progress_and_round_limit_stop_further_writes(tmp_path, kind):
    async def go():
        m, s, c, sent = await ready(tmp_path, (0, 0) if kind == "zero" else (0.5, 0.5))
        if kind == "limit":
            c.max_rounds = 1
        await c.process(await m.cycle())
        x = await c.process(await m.cycle())
        assert x[0]["status"] == (
            "RESIDUAL_NO_FILL_PROGRESS" if kind == "zero" else "RESIDUAL_ATTEMPT_LIMIT"
        )
        assert len(sent) == 2 and m.stop.stopped

    asyncio.run(go())


def test_private_change_after_claim_blocks_both_sends_and_persists_hold(tmp_path):
    async def go():
        m, s, c, sent = await ready(tmp_path)
        original = c.claim

        async def claim(*args):
            x = await original(*args)
            s["a"]["positions"] = []
            return x

        c.claim = claim
        await c.process(await m.cycle())
        assert not sent and m.stop.stopped
        assert json.loads((await m.durable.get("t"))["payload"])["exit_hold_reason"]

    asyncio.run(go())


def test_unknown_submit_never_repeats(tmp_path):
    async def go():
        m, s, c, sent = await ready(tmp_path)

        async def fail(request):
            raise TimeoutError()

        c.executors["a"].inner.submit = fail
        x = await c.process(await m.cycle())
        assert "UNKNOWN" in x[0]["status"] and m.stop.stopped
        await c.process(await m.cycle())
        assert (
            len(sent) == 1
            and (await m.diary.order_intent_states())["t:exit-residual:1:a:sell"]
            == "UNKNOWN"
        )

    asyncio.run(go())


def test_inflight_round_cannot_start_another_after_only_one_leg_finishes(tmp_path):
    async def go():
        from app.live_order_intent import OrderIntent
        from app.exchange_executor import SubmitResult
        from dataclasses import replace

        m, s, c, sent = await ready(tmp_path)
        row = await m.durable.get("t")
        proof = evaluate(
            trade(), row, list((await m.diary.order_intents("t")).values()), s, 1000
        )
        assert (await c.claim(trade(), proof))[0] == 1
        intent = OrderIntent(
            "t:exit-residual:1:a:sell", "t", "a", SYMBOL, "sell", 2, True
        )
        await m.diary.save_order_intent_result(
            intent, "CANCELED", SubmitResult("new", "canceled", 1, 102, 0.1)
        )
        s["a"]["positions"] = [replace(s["a"]["positions"][0], qty=0.5, contracts=1)]
        fresh = evaluate(
            trade(),
            await m.durable.get("t"),
            list((await m.diary.order_intents("t")).values()),
            s,
            1000,
        )
        assert (await c.claim(trade(), fresh))[1] == "RESIDUAL_ROUND_UNRESOLVED"
        assert not sent

    asyncio.run(go())


@pytest.mark.parametrize(
    "change",
    [
        "initial_missing",
        "initial_running",
        "quote",
        "gate_after_claim",
        "history_race",
        "slippage",
        "cancel",
    ],
)
def test_unsafe_recovery_stage_never_repeats(tmp_path, change):
    async def go():
        m, s, c, sent = await ready(tmp_path)
        summary = await m.cycle()
        if change == "initial_missing":
            import aiosqlite

            async with aiosqlite.connect(m.diary.path) as db:
                await db.execute(
                    "DELETE FROM order_intents WHERE intent_id='t:exit-short'"
                )
                await db.commit()
        if change == "initial_running":
            await m.durable.phase("t", "EXIT_SUBMITTING", exit_dispatch_status=None)
        if change == "quote":

            async def fail(*args):
                raise ValueError("BOOK_STALE")

            c.market.quote = fail
        if change == "gate_after_claim":
            original = c.claim

            async def claim(*args):
                x = await original(*args)
                c.authority = lambda t: False
                return x

            c.claim = claim
        if change == "history_race":
            original = c.claim

            async def claim(*args):
                await fill(m, "late", "a", "sell", 0.1, 102, True)
                return await original(*args)

            c.claim = claim
        if change == "slippage":
            c.executors["a"].inner.price = 100
        if change == "cancel":
            original = c.claim

            async def claim(*args):
                x = await original(*args)
                # Simulates task cancellation after durable reservation.
                raise asyncio.CancelledError()

            c.claim = claim
        if change == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await c.process(summary)
            c.claim = original
        else:
            await c.process(summary)
        before = len(sent)
        await c.process(await m.cycle())
        assert len(sent) == before
        if change != "slippage":
            assert not sent
        else:
            assert m.stop.stopped and before == 2

    asyncio.run(go())


def test_initial_both_partial_exit_hands_off_to_private_verified_residual_close(
    tmp_path,
):
    from app.live_exit_dispatch import Coordinator as Exit

    async def go():
        m, s, c, sent = await ready(tmp_path)
        # Reuse the same actual journal/snapshot readers for a new OPEN position.
        import aiosqlite

        async with aiosqlite.connect(m.diary.path) as db:
            await db.execute("DELETE FROM order_intents WHERE reduce_only=1")
            await db.commit()
        await m.durable.phase("t", "OPEN", exit_dispatch_status=None)
        from dataclasses import replace

        for v in ("a", "b"):
            p = s[v]["positions"][0]
            s[v]["positions"] = [replace(p, contracts=p.contracts * 2, qty=p.qty * 2)]
            c.executors[v].inner.fill_ratio = 0.5
        summary = await m.cycle()
        summary["trades"][0]["exit_signal"] = "TARGET_CAPTURE"
        e = Exit(
            m.durable,
            c.executors,
            c.market,
            lambda t: True,
            m.stop,
            clock=lambda: 1000,
            residual_recovery=True,
        )
        assert (await e.process(summary))[
            0
        ].status == "EXIT_RESIDUAL_PENDING_RECONCILIATION"
        for ex in c.executors.values():
            ex.inner.fill_ratio = 1
        assert (await c.process(await m.cycle()))[0][
            "status"
        ] == "RESIDUAL_FILLS_PENDING_PRIVATE"
        assert len(sent) == 4 and len((await m.cycle())["closed"]) == 1

    asyncio.run(go())
