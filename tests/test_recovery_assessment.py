import asyncio, json
from dataclasses import replace
from types import SimpleNamespace as NS
import pytest
from test_recovery_market import Client, SYMBOL, fake_clock
from app.recovery_market import Reader
from app.recovery_assessment import assess, reduction_effects
from app.executor_plan import build
from app.fee_schedule import FeeSchedule, FeeRate
from app.exchange_executor import SubmitResult
from app.recovery_flow import RecoveryResult
from app.two_leg_runner import TwoLegResult
from app.live_entry_flow import execute
from app.mock_executor import MockExecutor
from app.safe_executor import SafeExecutor
from app.db import Diary
from app.live_trade_store import Store


def setup():
    plan = build(SYMBOL, "a", "b", 0.04, 0.01, 0.01, lambda x: x, lambda x: x)
    a, b = Client(size=0.01), Client(size=0.01)
    b.book["bids"] = [[110, 10], [109.9, 10]]
    b.book["asks"] = [[110.1, 10], [110.2, 10]]
    reader = Reader({"a": a, "b": b}, clock=lambda: 1000)
    l = SubmitResult("l", "closed", 4, 100, 0.001)
    s = SubmitResult("s", "canceled", 2, 110, 0.001)
    fees = FeeSchedule({v: FeeRate(0.0001, 0.0001) for v in ("a", "b")})
    return plan, l, s, reader, fees


def run(**kwargs):
    p, l, s, r, f = setup()
    options = dict(fees_verified=True, risk_verified=True, funding_cost=0)
    options.update(kwargs)
    return asyncio.run(assess(p, l, s, r, f, **options))


def test_counterfactual_net_includes_paid_fees_and_remaining_gap():
    result = run()
    assert result.action == "COMPLETE" and not result.release_authorized
    m = result.model
    assert m["paid_entry_fees"] == 0.002 and m["target_exit_gap"] == 5
    assert result.complete_net_model == pytest.approx(
        0.2 - 0.002 - 0.00022 - 0.0008404 - 0.02 - 0.021208
    )
    assert result.incremental_complete_net_model == pytest.approx(
        0.1 - 0.0005 - 0.00022 - 0.0004202 - 0.01 - 0.0044 - 0.008404
    )
    assert result.flatten_net == pytest.approx(-0.0007)
    assert result.complete_request.reduce_only is False
    assert result.flatten_request.reduce_only is True
    assert m["kind"] == "CONVERGENCE_MODEL_NOT_REALIZED_PNL"
    assert m["immediate_close_net_quote"] < 0
    assert 0 < m["break_even_capture_fraction"] < 0.5


@pytest.mark.parametrize(
    "kwargs",
    [
        {"fees_verified": False},
        {"fees_verified": 1},
        {"risk_verified": False},
        {"risk_verified": 1},
        {"funding_cost": None},
        {"funding_cost": -0.1},
        {"funding_cost": float("nan")},
        {"capture_fraction": 2},
        {"capture_fraction": True},
        {"safety_usd": -0.1},
        {"min_net_usd": float("inf")},
    ],
)
def test_unknown_or_invalid_completion_assumptions_recommend_reduction(kwargs):
    x = run(**kwargs)
    assert x.action == "FLATTEN" and x.complete_net_model is None
    assert x.flatten_request.reduce_only and not x.release_authorized


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        ({"funding_cost": 1}, "FULL_NET"),
        ({"max_notional_usd": 4}, "NOTIONAL_CAP"),
        ({"min_net_usd": 1}, "FULL_NET"),
        ({"min_improvement_usd": 1}, "INCREMENTAL_NET"),
    ],
)
def test_completion_must_pass_total_net_and_incremental_advantage(kwargs, reason):
    x = run(**kwargs)
    assert x.action == "FLATTEN" and reason in x.reason


def test_profitable_existing_half_pair_does_not_hide_bad_incremental_fill():
    async def go():
        p, l, s, r, f = setup()
        r.clients["b"].book.update(bids=[[101, 10]], asks=[[101.1, 10]])
        x = await assess(
            p,
            l,
            s,
            r,
            f,
            fees_verified=True,
            risk_verified=True,
            funding_cost=0,
            min_net_usd=0,
        )
        assert x.complete_net_model > 0
        assert x.incremental_complete_net_model < 0 and x.action == "FLATTEN"

    asyncio.run(go())


def test_immediate_reduction_profit_can_outweigh_completion_model():
    async def go():
        p, l, s, r, f = setup()
        r.clients["a"].book.update(bids=[[105, 10]], asks=[[105.1, 10]])
        x = await assess(
            p, l, s, r, f, fees_verified=True, risk_verified=True, funding_cost=0
        )
        assert x.complete_net_model > 0
        assert (
            x.flatten_net > x.incremental_complete_net_model and x.action == "FLATTEN"
        )

    asyncio.run(go())


@pytest.mark.parametrize("problem", ["stale", "shallow"])
def test_unverified_completion_or_future_exit_depth_keeps_reduce_only(problem):
    async def go():
        p, l, s, r, f = setup()
        if problem == "stale":
            r.clients["b"].book["timestamp"] = 990000
        else:
            r.clients["b"].book.update(bids=[[110, 3]], asks=[[110.1, 3]])
        x = await assess(
            p, l, s, r, f, fees_verified=True, risk_verified=True, funding_cost=0
        )
        assert x.action == "FLATTEN" and "QUOTE_UNVERIFIED" in x.reason

    asyncio.run(go())


@pytest.mark.parametrize("case", ["working", "overfill", "nan", "missing_fee"])
def test_unknown_initial_fill_never_produces_additional_order(case):
    async def go():
        p, l, s, r, f = setup()
        if case == "working":
            s = replace(s, status="open")
        if case == "overfill":
            l = replace(l, filled=5)
        if case == "nan":
            l = replace(l, filled=float("nan"))
        if case == "missing_fee":
            l = replace(l, fee=None)
        x = await assess(p, l, s, r, f)
        assert x.action == "BLOCKED" and x.flatten_request is None

    asyncio.run(go())


@pytest.mark.parametrize("filled,remaining", [(2, 0.02), (1, 0.03), (0, 0.04)])
def test_realized_reduction_accounts_only_closed_excess(filled, remaining):
    p, l, s, _, _ = setup()
    rr = SubmitResult("r", "canceled", filled, 99 if filled else None, 0.001)
    effects = reduction_effects(
        p, TwoLegResult(l, s, False, 50), RecoveryResult("FLATTEN", filled == 2, rr)
    )
    assert effects["remaining_long_base"] == pytest.approx(remaining)
    assert effects["remaining_short_base"] == 0.02 and not effects["private_verified"]
    assert effects["net_excluding_funding"] == pytest.approx(
        -filled * 0.01 - 0.001 * filled / 4 - 0.001
    )


def test_live_partial_entry_reduces_excess_and_persists_assessment_and_cashflow(
    tmp_path, monkeypatch
):
    now = [1000]
    fake_clock(monkeypatch, now)

    async def go():
        p, l, s, reader, fees = setup()
        d = Diary(str(tmp_path / "db"))
        await d.init()
        durable = Store(d.path)
        await durable.init()

        class Capture(MockExecutor):
            def __init__(self):
                super().__init__(price=100)
                self.requests = []

            async def submit(self, r):
                self.requests.append(r)
                return await super().submit(r)

        a = Capture()
        b = MockExecutor(0.5, 110)
        ea, eb = SafeExecutor("a", a, d, lambda: True), SafeExecutor(
            "b", b, d, lambda: True
        )

        async def advisor(plan, lr, sr):
            return await assess(
                plan,
                lr,
                sr,
                reader,
                fees,
                fees_verified=True,
                risk_verified=True,
                funding_cost=0,
            )

        kwargs = dict(
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
        x = await execute(
            SYMBOL,
            p,
            ea,
            eb,
            100,
            110,
            10,
            0.01,
            fees,
            0.1,
            kwargs,
            private_snapshot=lambda: {},
            durable_store=durable,
            trade_id_hint="t",
            recovery_market_reader=reader,
            recovery_assessor=advisor,
        )
        assert not x.opened and x.recovery.action == "FLATTEN"
        assert (
            len(a.requests) == 2
            and a.requests[-1].reduce_only
            and a.requests[-1].qty == 2
        )
        row = await durable.get("t")
        payload = json.loads(row["payload"])
        assert payload["recovery_assessment"]["action"] == "COMPLETE"
        assert not payload["recovery_assessment"]["release_authorized"]
        assert payload["recovery_effects"]["remaining_long_base"] == 0.02
        assert (
            payload["entry_hold_reason"]
            == "ENTRY_REDUCTION_REQUIRES_PRIVATE_ACCOUNTING"
        )
        again = await execute(
            SYMBOL,
            p,
            ea,
            eb,
            100,
            110,
            10,
            0.01,
            fees,
            0.1,
            kwargs,
            private_snapshot=lambda: {},
            durable_store=durable,
            trade_id_hint="t",
            recovery_market_reader=reader,
        )
        assert again.reason == "DUPLICATE_DURABLE_TRADE" and len(a.requests) == 2

    asyncio.run(go())


def test_read_only_monitor_card_separates_reduction_from_final_result():
    from app.live_monitor_view import positions

    summary = {
        "trades": [
            dict(
                symbol=SYMBOL,
                trade_id="t",
                long_venue="a",
                short_venue="b",
                phase="UNKNOWN",
                recovery_effects=dict(
                    reduced_base=0.02,
                    net_excluding_funding=-0.01,
                    remaining_long_base=0.02,
                    remaining_short_base=0.02,
                ),
                recovery_assessment=dict(action="COMPLETE<script>"),
            )
        ]
    }
    text = positions(summary)
    assert "Результат сокращения без funding: -0.0100 USD" in text
    assert "Итог сделки ещё не подтверждён" in text
    assert "модель, без разрешения на ордер" in text
    assert "<script>" not in text and "&lt;script&gt;" in text


def test_zero_fill_unknown_reduction_fee_is_not_silently_zero():
    p, l, s, _, _ = setup()
    with pytest.raises(ValueError, match="EVIDENCE"):
        reduction_effects(
            p,
            TwoLegResult(l, s, False, 50),
            RecoveryResult(
                "FLATTEN", False, SubmitResult("r", "canceled", 0, None, None)
            ),
        )
