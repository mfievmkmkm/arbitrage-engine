import asyncio, time
from dataclasses import replace
from types import SimpleNamespace as NS
import pytest
from app.order_settlement import settle
from app.exchange_executor import SubmitResult
from app.hybrid_entry import FallbackQuote, assess
from app.native_order_plan import prepare_pair
from app.executor_plan import build
from app.live_entry_flow import execute
from app.safe_executor import SafeExecutor
from app.db import Diary
from app.live_trade_store import Store
from app.fee_schedule import FeeSchedule, FeeRate
from app.private_adapter import PrivatePosition
from test_native_order_plan import client, SYMBOL


def plan():
    return build(SYMBOL, "binance", "bybit", 0.04, 0.01, 0.01, lambda x: x, lambda x: x)


def proof(lp=100, sp=110):
    return FallbackQuote(
        prepare_pair(SYMBOL, "binance", "bybit", client(), client(), 0.04, lp, sp),
        lp,
        sp,
        time.time(),
        True,
    )


ZERO = SubmitResult("zero", "canceled", 0, None, 0)


@pytest.mark.parametrize(
    "case", ["partial", "working", "stale", "future", "slip", "qty", "bool", "fee"]
)
def test_fallback_never_treats_uncertainty_or_partial_as_zero_fill(case):
    p = plan()
    q = proof()
    r = ZERO
    if case == "partial":
        r = SubmitResult("one", "canceled", 1, 100, 0)
    if case == "working":
        r = replace(ZERO, status="open")
    if case == "stale":
        q = replace(q, book_ts=time.time() - 2)
    if case == "future":
        q = replace(q, book_ts=time.time() + 2)
    if case == "slip":
        q = proof(101, 110)
    if case == "qty":
        q = replace(q, native=replace(q.native, base_qty=0.05))
    if case == "bool":
        q = replace(q, books_verified="true")
    if case == "fee":
        r = replace(ZERO, fee=0.1)
    assert not assess(r, ZERO, p, q, 100, 110)[0]


@pytest.mark.parametrize(
    "case", ["decrease", "overfill", "identity", "nan", "missing_price", "fee"]
)
def test_cancel_response_must_preserve_cumulative_evidence(case):
    async def go():
        initial = SubmitResult("1", "open", 0.5, 100, 0)
        canceled = SubmitResult("1", "canceled", 0.6, 100, 0)
        if case == "decrease":
            canceled = replace(canceled, filled=0.4)
        if case == "overfill":
            canceled = replace(canceled, filled=2)
        if case == "identity":
            canceled = replace(canceled, order_id="other")
        if case == "nan":
            canceled = replace(canceled, filled=float("nan"))
        if case == "missing_price":
            canceled = replace(canceled, avg_price=None)
        if case == "fee":
            canceled = replace(canceled, fee=None)

        class Ex:
            async def cancel(self, *args):
                return canceled

        result, reason = await settle(Ex(), initial, SYMBOL, 1)
        assert result is None and reason == "CANCEL_EVIDENCE_CONFLICT"

    asyncio.run(go())


def test_cancel_can_include_additional_fill_without_losing_it():
    async def go():
        class Ex:
            async def cancel(self, *args):
                return SubmitResult("1", "canceled", 0.6, 101, 0.1)

        r, reason = await settle(
            Ex(), SubmitResult("1", "open", 0.5, 100, 0), SYMBOL, 1
        )
        assert r.filled == 0.6 and reason == "TERMINAL"

    asyncio.run(go())


def admission():
    return dict(
        live_enabled=True,
        release_gate=NS(micro_live=True),
        startup_safe=True,
        private_streams=True,
        withdrawals_disabled=True,
        bankroll=50,
        daily_loss=0,
        books_fresh=True,
        risk_ok=True,
    )


def snapshot():
    return {
        v: dict(health=NS(ok=True), positions=[PrivatePosition(v, SYMBOL, side, 0.04)])
        for v, side in (("binance", "long"), ("bybit", "short"))
    }


class Inner:
    def __init__(self, price, unknown=False):
        self.price = price
        self.requests = []
        self.unknown = unknown

    async def submit(self, r):
        self.requests.append(r)
        if r.order_type == "limit":
            return SubmitResult(str(len(self.requests)), "canceled", 0, None, 0)
        if self.unknown:
            raise TimeoutError()
        return SubmitResult(str(len(self.requests)), "closed", r.qty, self.price, 0)


@pytest.mark.parametrize(
    "case", ["success", "default_off", "stale", "net", "unknown", "actual_slip"]
)
def test_hybrid_entry_uses_distinct_durable_intents_and_never_resends_unknown(
    tmp_path, case
):
    async def go():
        d = Diary(str(tmp_path / "d.db"))
        await d.init()
        durable = Store(d.path)
        await durable.init()
        ia, ib = Inner(101 if case == "actual_slip" else 100), Inner(
            110, case == "unknown"
        )
        a, b = SafeExecutor("binance", ia, d, lambda: True), SafeExecutor(
            "bybit", ib, d, lambda: True
        )

        async def requote():
            q = proof(100.2, 109.8) if case == "net" else proof()
            return replace(q, book_ts=time.time() - 5) if case == "stale" else q

        args = dict(
            symbol=SYMBOL,
            plan=plan(),
            long_executor=a,
            short_executor=b,
            long_price=100,
            short_price=110,
            edge_pct=10,
            book_spread_pct=0.01,
            fee_schedule=FeeSchedule(
                {"binance": FeeRate(0, 0), "bybit": FeeRate(0, 0)}
            ),
            min_net_edge_usd=0.39 if case == "net" else 0.1,
            admission_kwargs=admission(),
            private_snapshot=snapshot,
            trade_id_hint="trade",
            durable_store=durable,
            hybrid_requote=None if case == "default_off" else requote,
        )
        result = await execute(**args)
        if case == "success":
            assert result.opened and result.actual.base_qty == 0.04
            assert len(ia.requests) == len(ib.requests) == 2
            assert (
                ia.requests[1].price is None
                and ia.requests[1].reference_price == 100
                and not ia.requests[1].ioc
            )
            intents = await d.order_intents()
            assert len(intents) == 4 and "trade:entry-market-long" in intents
            from app.live_recovery_evidence import rebuild

            row = await durable.get("trade")
            rebuilt = rebuild(
                row,
                __import__("json").loads(row["payload"]),
                list(intents.values()),
                snapshot(),
            )
            assert rebuilt.base_qty == 0.04 and rebuilt.long_entry == 100
        elif case == "actual_slip":
            assert (
                not result.opened and result.reason == "FALLBACK_ACTUAL_SLIPPAGE_STOP"
            )
            import json

            assert (
                json.loads((await durable.get("trade"))["payload"])["entry_hold_reason"]
                == result.reason
            )
            assert len(ia.requests) == len(ib.requests) == 2
        elif case == "unknown":
            assert not result.opened and "FALLBACK_UNCERTAIN" in result.reason
            assert (await d.order_intent_states())[
                "trade:entry-market-short"
            ] == "UNKNOWN"
        else:
            assert not result.opened and len(ia.requests) == len(ib.requests) == 1
        # A restart using the same durable identity never sends either stage again.
        before = (len(ia.requests), len(ib.requests))
        again = await execute(**args)
        assert (
            not again.opened
            and again.reason == "DUPLICATE_DURABLE_TRADE"
            and before == (len(ia.requests), len(ib.requests))
        )

    asyncio.run(go())
