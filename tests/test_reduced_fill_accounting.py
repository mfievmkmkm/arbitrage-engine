import asyncio, json
from copy import deepcopy
from dataclasses import replace
import pytest
from test_live_monitor_integration import setup, fill
from app.live_recovery_evidence import rebuild, exit_accounting, Unverified
from app.reduced_fill_accounting import basis, cycle
from app.live_pnl import calculate
from app.live_trade_lifecycle import close
from app.mock_executor import MockExecutor
from app.safe_executor import SafeExecutor
from app.live_order_intent import OrderIntent
from app.exchange_executor import SubmitResult


async def reduced(m):
    await fill(m, "l", "a", "buy", 6, 100, fee=0.3)
    await fill(m, "s", "b", "sell", 2, 105, fee=0.2)
    await fill(m, "t:entry-recovery:a:sell", "a", "sell", 2, 99, True, 0.1)
    await m.durable.phase(
        "t", "UNKNOWN", entry_hold_reason="ENTRY_REDUCTION_REQUIRES_PRIVATE_ACCOUNTING"
    )


async def evidence(m):
    row = await m.durable.get("t")
    return (
        row,
        json.loads(row["payload"]),
        list((await m.diary.order_intents()).values()),
    )


def test_reduced_pair_is_rebuilt_with_original_fees_and_realized_loss(tmp_path):
    async def go():
        m, snapshot = await setup(tmp_path)
        await reduced(m)
        row, payload, intents = await evidence(m)
        t = rebuild(row, payload, intents, snapshot)
        assert t.base_qty == 2 and t.long_contracts == 4 and t.short_contracts == 2
        assert t.entry_fees == 0.5 and t.recovery_gross == -1 and t.recovery_fees == 0.1
        assert t.recovery_capital == 300
        summary = await m.cycle()
        assert summary["trades"][0]["estimated_net"] == pytest.approx(6.5)
        assert summary["trades"][0]["gross"] == 7
        assert summary["trades"][0]["target_net_model"] == pytest.approx(8.5 * 0.7)
        assert summary["trades"][0]["exit_signal"] == "TARGET_CAPTURE"
        assert m.stop.stopped and len(m.runtime.load()) == 1
        m.runtime.save([])
        await m.cycle()
        assert m.runtime.load()[0].recovery_gross == -1

    asyncio.run(go())


def test_reduced_cycle_close_matches_full_cashflow_and_is_idempotent(tmp_path):
    async def go():
        m, snapshot = await setup(tmp_path)
        await reduced(m)
        await m.cycle()
        t = m.runtime.load()[0]
        await fill(m, "exit-l", "a", "sell", 4, 102, True, 0.2)
        await fill(m, "exit-s", "b", "buy", 2, 103, True, 0.2)
        row, payload, intents = await evidence(m)
        r = exit_accounting(t, intents)(0.3)
        assert r.gross == 7 and r.fees == 1 and r.net == pytest.approx(6.3)
        assert r.roi_pct == pytest.approx(6.3 / 300 * 100)
        for value in snapshot.values():
            value["positions"] = []
        summary = await m.cycle()
        assert len(summary["closed"]) == 1 and summary["closed"][0][
            "net"
        ] == pytest.approx(6.3)
        assert m.runtime.load() == []
        assert not (await m.cycle())["closed"]

    asyncio.run(go())


def test_full_single_leg_abort_can_close_without_inventing_a_hedged_trade(tmp_path):
    async def go():
        m, _ = await setup(tmp_path, flat=True)
        await fill(m, "l", "a", "buy", 2, 100, fee=0.2)
        i = OrderIntent("s", "t", "b", "X/USDT:USDT", "sell", 2, False)
        await m.diary.save_order_intent_result(
            i, "CANCELED", SubmitResult("s", "canceled", 0, None, 0.02)
        )
        await fill(m, "t:entry-recovery:a:sell", "a", "sell", 2, 99, True, 0.1)
        summary = await m.cycle()
        assert len(summary["closed"]) == 1
        r = summary["closed"][0]
        assert r["gross"] == -1 and r["fees"] == pytest.approx(0.32)
        assert r["net"] == pytest.approx(-1.02)
        assert m.runtime.load() == []

    asyncio.run(go())


def test_private_flat_waits_for_funding_before_reduced_result(tmp_path):
    async def go():
        m, snapshot = await setup(tmp_path)
        await reduced(m)
        await m.cycle()
        await fill(m, "exit-l", "a", "sell", 4, 102, True, 0.2)
        await fill(m, "exit-s", "b", "buy", 2, 103, True, 0.2)
        for value in snapshot.values():
            value["positions"] = []
        m.funding.verified = False
        assert not (await m.cycle())["closed"]
        assert (await m.durable.get("t"))["phase"] != "CLOSED_PRIVATE_VERIFIED"
        m.funding.verified = True
        assert len((await m.cycle())["closed"]) == 1

    asyncio.run(go())


@pytest.mark.parametrize(
    "problem",
    [
        "unknown",
        "missing_fee",
        "nan_fee",
        "overfill",
        "duplicate_id",
        "duplicate_order",
        "wrong_side",
        "wrong_symbol",
        "wrong_trade",
        "bad_flag",
        "excess_reduction",
        "other_leg_reduction",
        "unbalanced_residual",
        "private_mismatch",
        "other_exit",
    ],
)
def test_uncertain_reduced_history_does_not_rebuild(problem, tmp_path):
    async def go():
        m, snapshot = await setup(tmp_path)
        await reduced(m)
        row, payload, intents = await evidence(m)
        if problem == "unknown":
            intents[-1]["state"] = "UNKNOWN"
        if problem == "missing_fee":
            intents[0]["fee"] = None
        if problem == "nan_fee":
            intents[0]["fee"] = float("nan")
        if problem == "overfill":
            intents[-1]["filled"] = 3
        if problem == "duplicate_id":
            intents.append(deepcopy(intents[0]))
        if problem == "duplicate_order":
            intents[-1]["order_id"] = intents[0]["order_id"]
        if problem == "wrong_side":
            intents[0]["side"] = "sell"
        if problem == "wrong_symbol":
            intents[0]["symbol"] = "OTHER"
        if problem == "wrong_trade":
            intents[0]["trade_id"] = "other"
        if problem == "bad_flag":
            intents[0]["reduce_only"] = 2
        if problem == "excess_reduction":
            intents[-1].update(qty=3, filled=3)
        if problem == "other_leg_reduction":
            intents[-1].update(
                venue="b", side="buy", intent_id="t:entry-recovery:b:buy"
            )
        if problem == "unbalanced_residual":
            intents[-1]["filled"] = 1
        if problem == "private_mismatch":
            snapshot["a"]["positions"][0].qty = 3
        if problem == "other_exit":
            intents[-1]["intent_id"] = "t:exit-long"
        with pytest.raises(Unverified):
            rebuild(row, payload, intents, snapshot)

    asyncio.run(go())


def test_runtime_recovery_numbers_cannot_replace_fill_evidence(tmp_path):
    async def go():
        m, _ = await setup(tmp_path)
        await reduced(m)
        await m.cycle()
        t = m.runtime.load()[0]
        t.recovery_gross = 100
        await m.durable.phase("t", "OPEN", runtime_trade=t.row())
        result = await m.cycle()
        assert any(
            x["code"] == "REDUCED_RUNTIME_ACCOUNTING_MISMATCH"
            for x in result["incidents"]
        )
        assert not result["closed"] and m.stop.stopped

    asyncio.run(go())


def test_prepared_close_pnl_includes_prior_reduction_once(tmp_path):
    async def go():
        m, _ = await setup(tmp_path)
        await reduced(m)
        await m.cycle()
        t = m.runtime.load()[0]
        a = SafeExecutor("a", MockExecutor(price=102), m.diary, lambda: True)
        b = SafeExecutor("b", MockExecutor(price=103), m.diary, lambda: True)

        async def flat():
            return {
                v: dict(health=type("H", (), {"ok": True})(), positions=[])
                for v in ("a", "b")
            }

        result = await close(t, a, b, flat, attempts=1, delay=0, funding=0.3)
        assert result.closed and result.result.gross == 7
        assert result.result.fees == pytest.approx(
            0.6
        ) and result.result.net == pytest.approx(6.7)

    asyncio.run(go())


def test_runtime_recovery_profit_requires_reduction_intent(tmp_path):
    from test_live_monitor_integration import entries, trade

    async def go():
        m, _ = await setup(tmp_path)
        await entries(m)
        t = trade()
        t.recovery_gross = 100
        await m.durable.phase("t", "OPEN", runtime_trade=t.row())
        result = await m.cycle()
        assert any(x["code"] == "REDUCED_EVIDENCE_MISSING" for x in result["incidents"])
        assert m.stop.stopped

    asyncio.run(go())


@pytest.mark.parametrize("fee", [None, 0.02, float("nan")])
def test_zero_fill_costs_cannot_be_discarded_as_free_abort(tmp_path, fee):
    async def go():
        m, _ = await setup(tmp_path, flat=True)
        i = OrderIntent("z", "t", "a", "X/USDT:USDT", "buy", 1, False)
        await m.diary.save_order_intent_result(
            i, "CANCELED", SubmitResult("z", "canceled", 0, None, fee)
        )
        result = await m.cycle()
        assert (await m.durable.get("t"))["phase"] != "ABORTED"
        assert not result["closed"] and m.stop.stopped

    asyncio.run(go())


def test_short_surplus_reduction_uses_buy_cashflow_with_correct_sign(tmp_path):
    async def go():
        m, snapshot = await setup(tmp_path)
        await fill(m, "l", "a", "buy", 4, 100, fee=0.2)
        await fill(m, "s", "b", "sell", 3, 105, fee=0.3)
        await fill(m, "t:entry-recovery:b:buy", "b", "buy", 1, 106, True, 0.1)
        row, payload, intents = await evidence(m)
        t = rebuild(row, payload, intents, snapshot)
        assert t.recovery_gross == -1 and t.recovery_capital == 315
        await fill(m, "exit-l", "a", "sell", 4, 102, True, 0.2)
        await fill(m, "exit-s", "b", "buy", 2, 103, True, 0.2)
        row, payload, intents = await evidence(m)
        result = exit_accounting(t, intents)(0.3)
        assert result.gross == 7 and result.net == pytest.approx(6.3)

    asyncio.run(go())


@pytest.mark.parametrize(
    "field,value",
    [
        ("long_contract_size", None),
        ("long_contract_size", True),
        ("short_contract_size", float("nan")),
        ("opened_at", None),
        ("opened_at", 0),
    ],
)
def test_reduced_rebuild_requires_explicit_units_and_original_time(
    tmp_path, field, value
):
    async def go():
        m, snapshot = await setup(tmp_path)
        await reduced(m)
        row, payload, intents = await evidence(m)
        payload[field] = value
        with pytest.raises(Unverified):
            rebuild(row, payload, intents, snapshot)

    asyncio.run(go())


def test_reduced_target_requires_verified_fees_and_funding(tmp_path):
    async def go():
        m, _ = await setup(tmp_path)
        await reduced(m)
        m.funding.verified = False
        summary = await m.cycle()
        assert summary["trades"][0]["target_net_model"] is None
        assert summary["trades"][0]["exit_signal"] == "HOLD"

    asyncio.run(go())


def test_tiny_contract_amount_is_not_treated_as_zero_exit(tmp_path):
    async def go():
        m, _ = await setup(tmp_path)
        await fill(m, "l", "a", "buy", 1e-12, 100, fee=0)
        await fill(m, "s", "b", "sell", 1e-12, 105, fee=0)
        row, payload, intents = await evidence(m)
        with pytest.raises(Unverified, match="QUANTITY_MISMATCH"):
            cycle(row, payload, intents)

    asyncio.run(go())


def test_contract_size_overflow_cannot_create_nonfinite_basis(tmp_path):
    async def go():
        m, _ = await setup(tmp_path)
        await reduced(m)
        row, payload, intents = await evidence(m)
        payload["long_contract_size"] = 1e308
        with pytest.raises(Unverified):
            basis(row, payload, intents)

    asyncio.run(go())
