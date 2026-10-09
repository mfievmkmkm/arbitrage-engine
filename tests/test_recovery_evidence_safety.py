import asyncio
from dataclasses import replace
import pytest
from app.exchange_executor import SubmitResult
from app.mock_executor import MockExecutor
from app.recovery_flow import recover
from app.close_recovery_executor import recover_close
from app.runtime_state import RuntimeTrade
from app.exit_runner import ExitResult
from app.private_close_recovery import recover_from_private
from app.private_adapter import PrivatePosition


class Response(MockExecutor):
    def __init__(self, response):
        super().__init__()
        self.response = response
        self.cancels = 0

    async def submit(self, request):
        return self.response

    async def cancel(self, order_id, symbol):
        self.cancels += 1
        return replace(self.response, status="canceled")


@pytest.mark.parametrize(
    "filled,price,fee",
    [(float("nan"), 100, 0), (2, 100, 0), (1, None, 0), (1, 100, float("nan"))],
)
def test_recovery_rejects_invalid_fill_evidence(filled, price, fee):
    async def go():
        ex = Response(SubmitResult("r", "closed", filled, price, fee))
        result = await recover("X", "a", "b", 1, 0, 1, 1, MockExecutor(), ex, 1, 0, 2)
        assert not result.completed and result.error == "ORDER_EVIDENCE_INVALID"

    asyncio.run(go())


def test_rounded_down_recovery_reports_remaining_exposure():
    async def go():
        ex = MockExecutor()
        result = await recover(
            "X",
            "a",
            "b",
            1,
            0,
            1,
            1,
            MockExecutor(),
            ex,
            1,
            0,
            2,
            short_round=lambda q: 0.9,
        )
        assert not result.completed and result.result.filled == 0.9
        assert result.error == "RECOVERY_RESIDUAL"

    asyncio.run(go())


def test_rounder_cannot_increase_recovery_quantity():
    async def go():
        ex = MockExecutor()
        result = await recover(
            "X",
            "a",
            "b",
            1,
            0,
            1,
            1,
            MockExecutor(),
            ex,
            1,
            0,
            2,
            short_round=lambda q: 2,
        )
        assert not result.completed and ex.seq == 0

    asyncio.run(go())


def test_partial_close_recovery_retains_fills_prices_and_fees():
    async def go():
        trade = RuntimeTrade("t", "X", "a", "b", 2, 2, 2, 1, 1, 100, 110, 0)
        original = ExitResult(
            SubmitResult("l", "canceled", 1, 100, 0.1),
            SubmitResult("s", "closed", 2, 110, 0.2),
            False,
            0,
        )
        ex = Response(SubmitResult("r", "open", 0.5, 104, 0.05))
        result = await recover_close(trade, original, ex, MockExecutor())
        assert not result.recovered and result.reason == "RECOVERY_PARTIAL"
        assert result.execution.long_result.filled == 1.5
        assert result.execution.long_result.fee == pytest.approx(0.15)
        assert result.execution.long_result.avg_price == pytest.approx((100 + 52) / 1.5)
        assert ex.cancels == 1

    asyncio.run(go())


class Health:
    ok = True


@pytest.mark.parametrize(
    "positions",
    [
        None,
        [PrivatePosition("a", "X", "long", 1, contracts=float("nan"))],
        [PrivatePosition("a", "X", "long", 1, contracts=None, contract_size=0)],
    ],
)
def test_private_recovery_rejects_unknown_position_units(positions):
    async def go():
        trade = RuntimeTrade("t", "X", "a", "b", 2, 2, 2, 1, 1, 100, 110, 0)
        ex = MockExecutor()
        snapshot = {
            "a": {"health": Health(), "positions": positions},
            "b": {"health": Health(), "positions": []},
        }
        result = await recover_from_private(trade, snapshot, ex, ex)
        assert not result.recovered and result.reason == "PRIVATE_STATE_UNTRUSTED"
        assert ex.seq == 0

    asyncio.run(go())


def test_private_recovery_retains_both_partial_results():
    async def go():
        trade = RuntimeTrade("t", "X", "a", "b", 2, 2, 2, 1, 1, 100, 110, 0)
        snapshot = {
            venue: {
                "health": Health(),
                "positions": [
                    PrivatePosition(venue, "X", side, 1, contracts=1, contract_size=1)
                ],
            }
            for venue, side in (("a", "long"), ("b", "short"))
        }
        result = await recover_from_private(
            trade, snapshot, MockExecutor(0.5), MockExecutor(0.5)
        )
        assert not result.recovered
        assert result.long_result.filled == 0.5 and result.short_result.filled == 0.5
        assert result.long_result.status == result.short_result.status == "canceled"

    asyncio.run(go())


def test_private_recovery_uses_durable_intent_instead_of_raw_submit():
    class IntentExecutor(MockExecutor):
        async def submit(self, request):
            raise AssertionError("raw submit forbidden")

        async def submit_intent(self, intent, request):
            assert intent.reduce_only and request.reduce_only
            assert request.client_order_id == "t:close-recovery:a:sell"
            return SubmitResult("r", "closed", request.qty, 100, 0.1), "FILLED"

    async def go():
        trade = RuntimeTrade("t", "X", "a", "b", 2, 2, 2, 1, 1, 100, 110, 0)
        snapshot = {
            "a": {
                "health": Health(),
                "positions": [
                    PrivatePosition("a", "X", "long", 1, contracts=1, contract_size=1)
                ],
            },
            "b": {"health": Health(), "positions": []},
        }
        result = await recover_from_private(
            trade, snapshot, IntentExecutor(), MockExecutor()
        )
        assert result.recovered and result.long_result.fee == 0.1

    asyncio.run(go())
