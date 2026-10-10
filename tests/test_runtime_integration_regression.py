import asyncio
import json
from types import SimpleNamespace
from app.db import Diary
from app.live_trade_store import Store
from app.runtime_store import RuntimeStore
from app.runtime_state import RuntimeTrade
from app.live_startup_recovery import recover
from app.live_service import LiveService
from app.trade_journal import TradeJournal
from app.safe_executor import SafeExecutor
from app.mock_executor import MockExecutor
from app.exchange_executor import SubmitRequest
from app.live_order_intent import OrderIntent
from app.executor_plan import build
from app.fee_schedule import FeeSchedule, FeeRate
from app.private_adapter import PrivatePosition
from app.live_entry_flow import execute


class Healthy:
    ok = True


def snapshot(base=0.04):
    return {
        "a": {
            "health": Healthy(),
            "positions": [PrivatePosition("a", "X", "long", base)] if base else [],
        },
        "b": {
            "health": Healthy(),
            "positions": [PrivatePosition("b", "X", "short", base)] if base else [],
        },
    }


def entry_args(a, b):
    plan = build("X", "a", "b", 0.04, 0.01, 0.01, lambda x: x, lambda x: x)
    return dict(
        symbol="X",
        plan=plan,
        long_executor=a,
        short_executor=b,
        long_price=100,
        short_price=110,
        edge_pct=10,
        book_spread_pct=0.01,
        fee_schedule=FeeSchedule({"a": FeeRate(0, 0), "b": FeeRate(0, 0)}),
        min_net_edge_usd=0.1,
        private_snapshot=snapshot,
        admission_kwargs=dict(
            live_enabled=True,
            release_gate=SimpleNamespace(micro_live=True),
            startup_safe=True,
            private_streams=True,
            withdrawals_disabled=True,
            bankroll=50,
            daily_loss=0,
            books_fresh=True,
            risk_ok=True,
        ),
    )


async def infrastructure(tmp_path):
    diary = Diary(str(tmp_path / "audit.db"))
    await diary.init()
    durable = Store(diary.path)
    await durable.init()
    runtime = RuntimeStore(str(tmp_path / "runtime.json"))
    return diary, durable, runtime


def test_live_facade_is_write_ahead_and_rebuilds_missing_runtime(tmp_path):
    async def go():
        diary, durable, runtime = await infrastructure(tmp_path)

        class Inspect(MockExecutor):
            async def submit(self, request):
                rows = await durable.active()
                assert rows[0]["phase"] == "ENTRY_SUBMITTING"
                assert (await diary.order_intent_states())[
                    request.client_order_id
                ] == "SUBMITTING"
                return await super().submit(request)

        a = SafeExecutor("a", Inspect(1, 100), diary, lambda: True)
        b = SafeExecutor("b", Inspect(1, 110), diary, lambda: True)
        args = entry_args(a, b)
        service = LiveService(runtime, TradeJournal(diary))
        result = await service.open(
            [],
            args,
            dict(
                plan=args["plan"],
                symbol="X",
                long_venue="a",
                short_venue="b",
                opened_at=1,
            ),
        )
        assert result.ok
        row = await durable.get(result.trade.trade_id)
        assert (
            row["phase"] == "OPEN"
            and row["planned_long"] == 4
            and row["actual_long"] == 4
        )
        assert json.loads(row["payload"])["runtime_trade"]["base_qty"] == 0.04
        (tmp_path / "runtime.json").unlink()
        recovery = await recover(durable, runtime, diary, True)
        assert not recovery[
            "safe"
        ]  # stored exposure still needs fresh private reconciliation
        assert runtime.load()[0].trade_id == result.trade.trade_id

    asyncio.run(go())


def test_timeout_persists_unknown_and_never_retries_same_intent(tmp_path):
    async def go():
        diary, durable, runtime = await infrastructure(tmp_path)

        class Uncertain(MockExecutor):
            async def submit(self, request):
                await super().submit(request)
                raise TimeoutError("ack lost")

        inner = Uncertain()
        safe = SafeExecutor("a", inner, diary, lambda: True)
        intent = OrderIntent("i", "t", "a", "X", "buy", 1, False)
        request = SubmitRequest("X", "buy", 1)
        assert (await safe.submit_intent(intent, request))[
            1
        ] == "SUBMIT_UNKNOWN_RECONCILE"
        assert (await safe.submit_intent(intent, request))[
            1
        ] == "DUPLICATE_OR_UNRESOLVED_INTENT"
        assert inner.seq == 1 and (await diary.order_intent_states())["i"] == "UNKNOWN"
        assert not (await recover(durable, runtime, diary, True))["safe"]

    asyncio.run(go())


def test_atomic_intent_reservation_prevents_concurrent_duplicate(tmp_path):
    async def go():
        diary, _, _ = await infrastructure(tmp_path)
        inner = MockExecutor()
        a = SafeExecutor("a", inner, diary, lambda: True)
        b = SafeExecutor("a", inner, diary, lambda: True)
        intent = OrderIntent("i", "t", "a", "X", "buy", 1, False)
        request = SubmitRequest("X", "buy", 1)
        results = await asyncio.gather(
            a.submit_intent(intent, request), b.submit_intent(intent, request)
        )
        assert inner.seq == 1 and sum(x[0] is not None for x in results) == 1

    asyncio.run(go())


def test_missing_private_source_blocks_before_any_order(tmp_path):
    async def go():
        diary, _, _ = await infrastructure(tmp_path)
        inner = MockExecutor()
        a = SafeExecutor("a", inner, diary, lambda: True)
        b = SafeExecutor("b", inner, diary, lambda: True)
        args = entry_args(a, b)
        args["private_snapshot"] = None
        result = await execute(**args)
        assert (
            not result.opened
            and inner.seq == 0
            and not await diary.order_intent_states()
        )

    asyncio.run(go())


def test_partial_entry_recovery_works_through_safe_executor(tmp_path):
    async def go():
        diary, durable, runtime = await infrastructure(tmp_path)

        class FirstPartial(MockExecutor):
            async def submit(self, request):
                self.fill_ratio = 0.5 if self.seq == 0 else 1
                return await super().submit(request)

        a = SafeExecutor("a", MockExecutor(1, 100), diary, lambda: True)
        b = SafeExecutor("b", FirstPartial(1, 110), diary, lambda: True)
        args = entry_args(a, b)
        args["durable_store"] = durable
        result = await execute(**args)
        assert result.opened
        states = await diary.order_intent_states(result.trade_id)
        assert states[result.trade_id + ":entry-recovery:b:sell"] == "FILLED"

    asyncio.run(go())


def test_db_terminal_preserves_payload_and_cannot_reopen(tmp_path):
    async def go():
        _, store, _ = await infrastructure(tmp_path)
        await store.phase(
            "t", "ENTRY_SUBMITTING", symbol="X", long_venue="a", planned_long=2
        )
        await store.phase("t", "OPEN", actual_long=1)
        await store.phase("t", "CLOSED_PRIVATE_VERIFIED", reason="PRIVATE_FLAT")
        row = await store.get("t")
        assert (
            row["symbol"] == "X"
            and row["planned_long"] == 2
            and row["actual_long"] == 1
        )
        try:
            await store.phase("t", "ENTRY_SUBMITTING")
        except ValueError:
            pass
        else:
            raise AssertionError("terminal state reopened")

    asyncio.run(go())


def test_close_durable_before_cache_cleanup_and_duplicate_close_blocked(tmp_path):
    async def go():
        diary, durable, runtime = await infrastructure(tmp_path)
        trade = RuntimeTrade("t", "X", "a", "b", 1, 1, 1, 1, 1, 100, 110, 0)
        runtime.save([trade])
        await durable.phase("t", "OPEN", runtime_trade=trade.row())
        a = SafeExecutor("a", MockExecutor(1, 105), diary, lambda: True)
        b = SafeExecutor("b", MockExecutor(1, 105), diary, lambda: True)
        service = LiveService(runtime, TradeJournal(diary), durable)
        result = await service.close([trade], trade, a, b, lambda: snapshot(0))
        assert result.ok and not runtime.load()
        assert (await durable.get("t"))["phase"] == "CLOSED_PRIVATE_VERIFIED"
        duplicate = await service.close([trade], trade, a, b, lambda: snapshot(0))
        assert not duplicate.ok and a.inner.seq == 1

    asyncio.run(go())


def test_reduce_only_exit_has_separate_gate_and_intent_must_match(tmp_path):
    async def go():
        diary, _, _ = await infrastructure(tmp_path)
        safe = SafeExecutor(
            "a", MockExecutor(), diary, lambda: False, exit_gate=lambda: True
        )
        intent = OrderIntent("i", "t", "a", "X", "sell", 1, True)
        request = SubmitRequest("X", "sell", 1, reduce_only=True)
        assert (await safe.submit_intent(intent, request))[1] == "FILLED"
        wrong = OrderIntent("bad", "t", "a", "X", "sell", 100, True)
        assert (await safe.submit_intent(wrong, request))[
            1
        ] == "INTENT_REQUEST_MISMATCH"

    asyncio.run(go())


def test_restart_clears_cache_after_verified_durable_close(tmp_path):
    async def go():
        diary, durable, runtime = await infrastructure(tmp_path)
        trade = RuntimeTrade("t", "X", "a", "b", 1, 1, 1, 1, 1, 100, 110, 0)
        runtime.save([trade])
        await durable.phase("t", "CLOSED_PRIVATE_VERIFIED", runtime_trade=trade.row())
        result = await recover(durable, runtime, diary, True)
        assert result["safe"] and not runtime.load()

    asyncio.run(go())
