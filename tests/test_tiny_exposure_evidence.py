import asyncio
from types import SimpleNamespace as NS
import pytest
from app.exchange_executor import SubmitResult
from app.order_settlement import terminal, valid, settle
from app.order_status import normalize
from app.private_residual import verify
from app.runtime_private_reconcile import verify_trade
from app.private_entry_verify import verify as verify_entry
from test_live_monitor_integration import trade


def snapshot(q):
    return {
        v: dict(
            health=NS(ok=True), positions=[NS(symbol="X/USDT:USDT", side=side, qty=q)]
        )
        for v, side in [("a", "long"), ("b", "short")]
    }


@pytest.mark.parametrize("requested", [1e-6, 1e-10, 1e-12, 1e-15])
def test_zero_fill_small_working_order_is_canceled_before_recovery(requested):
    async def go():
        result = SubmitResult("id", "open", 0, None, 0)
        assert (
            not terminal(result, requested) and normalize("open", 0, requested) == "ACK"
        )
        sent = []

        class Ex:
            async def cancel(self, oid, symbol):
                sent.append(oid)
                return SubmitResult(oid, "canceled", 0, None, 0)

        r, status = await settle(Ex(), result, "X", requested)
        assert sent == ["id"] and r.status == "canceled" and status == "TERMINAL"
        assert not valid(
            SubmitResult("id", "canceled", 0, None, 0), requested, requested * 0.5
        )
        assert not valid(SubmitResult("id", "closed", requested * 2, 100, 0), requested)

    asyncio.run(go())


@pytest.mark.parametrize("qty", [1e-6, 1e-10, 1e-12, 1e-15])
def test_nonzero_small_private_position_is_never_flat(qty):
    assert not verify(snapshot(qty), "X/USDT:USDT", "a", "b").flat
    t = trade()
    t.base_qty = qty
    s = snapshot(0)
    assert not verify_trade(t, s).safe
    assert not asyncio.run(
        verify_entry(lambda: s, t.symbol, "a", "b", qty, attempts=1)
    ).verified


@pytest.mark.parametrize("qty", [float("nan"), float("inf"), True, None])
def test_invalid_private_quantity_cannot_grant_flat_or_runtime_match(qty):
    s = snapshot(qty)
    assert not verify(s, "X/USDT:USDT", "a", "b").flat
    assert not verify_trade(trade(), s).safe
    assert not asyncio.run(
        verify_entry(lambda: s, trade().symbol, "a", "b", 2, attempts=1)
    ).verified


def test_missing_position_list_does_not_prove_flat():
    s = snapshot(0)
    del s["a"]["positions"]
    assert not verify(s, trade().symbol, "a", "b").flat
    assert not verify_trade(trade(), s).safe


@pytest.mark.parametrize("qty", [1e-10, 1e-12, 1e-15])
def test_small_opposite_exposure_is_not_ignored(qty):
    s = snapshot(2)
    s["a"]["positions"].append(NS(symbol=trade().symbol, side="short", qty=qty))
    assert not verify_trade(trade(), s).safe
    assert not asyncio.run(
        verify_entry(lambda: s, trade().symbol, "a", "b", 2, attempts=1)
    ).verified


@pytest.mark.parametrize("qty", [1e-10, 1e-12, 1e-15])
def test_tiny_overfill_cannot_be_accepted_as_a_flat_exit(qty):
    from app.close_recovery import plan

    t = NS(long_contracts=qty, short_contracts=qty, long_venue="a", short_venue="b")
    x = NS(
        long_result=SubmitResult("l", "closed", qty * 2, 100, 0),
        short_result=SubmitResult("s", "closed", qty, 100, 0),
    )
    assert plan(t, x).reason == "INVALID_CLOSE_FILL_EVIDENCE"


@pytest.mark.parametrize("field", ["amount", "filled"])
def test_boolean_exchange_volume_is_not_financial_evidence(field):
    from app.ccxt_executor import CCXTExecutor
    from app.private_order_reader import Reader

    row = dict(
        id="x",
        amount=1,
        filled=1,
        status="closed",
        average=100,
        fee={"cost": 0, "currency": "USDT"},
    )
    row[field] = True
    with pytest.raises(RuntimeError):
        CCXTExecutor("a", None)._result(row)
    with pytest.raises(ValueError):
        Reader("a", None).parse(row)


@pytest.mark.parametrize("qty", [1e-10, 1e-12, 1e-15])
def test_small_single_leg_entry_is_not_called_hedged(qty):
    from app.recovery_executor import plan

    x = plan("a", "b", qty, 0, -1, 0, 0)
    assert x.action == "FLATTEN" and x.venue == "a" and x.base_amount == qty
