import asyncio
import json
import time
from types import SimpleNamespace as NS
import pytest
from app.db import Diary
from app.live_trade_store import Store
from app.live_monitor_store import Store as Results
from app.spot_future_live_preflight import Admission
from app.spot_future_live_session import Session
from app.private_funding_reader import Evidence
from app.spot_future_live_result import finalize
from app.spot_future_cashflow import rebuild
from tests.test_spot_live_units import client, SS, FS


class Clock:
    def __init__(self):
        self.now = time.time()

    def __call__(self):
        return self.now


def exchange(clock, spot, state):
    c = client()
    c.has.update(fetchTradingFee=True, fetchPositionMode=True, fetchFundingHistory=True)
    c.orders, c.sent = {}, []
    c.ratio, c.unknown, c.fee_currency, c.zero_fee = (
        1,
        False,
        "X" if spot else "USDT",
        False,
    )
    c.sell_price, c.buy_price = (99.99, 100) if spot else (110, 110.01)

    async def book(symbol, limit=20):
        return dict(
            symbol=symbol,
            timestamp=int(clock() * 1000),
            bids=[[c.sell_price, 10000]],
            asks=[[c.buy_price, 10000]],
        )

    async def balance():
        q = state["spot"] if spot else 0
        return {
            "X": dict(free=q, used=0, total=q),
            "USDT": dict(free=50, used=0, total=50),
        }

    async def open_orders(*args):
        return []

    async def positions():
        return [
            dict(symbol=FS, contracts=state["future"], side="short", entryPrice=110)
        ]

    async def fee(symbol):
        return dict(symbol=symbol, maker=0.001, taker=0.001)

    async def mode(symbol):
        return dict(hedged=False)

    async def funding_history(*args):
        return []

    async def create(symbol, kind, side, qty, price, params):
        c.sent.append((symbol, side, qty, dict(params)))
        filled = qty * c.ratio
        avg = price or (c.buy_price if side == "buy" else c.sell_price)
        if spot:
            fee = 0 if c.zero_fee else filled * 0.001
            state["spot"] += filled - fee if side == "buy" else -filled - fee
        else:
            fee = 0 if c.zero_fee else filled * 0.001 * avg * 0.001
            state["future"] += filled if side == "sell" else -filled
        row = dict(
            id=str(len(c.sent)),
            symbol=symbol,
            side=side,
            amount=qty,
            filled=filled,
            average=avg if filled else None,
            status="canceled",
            fee=dict(currency=c.fee_currency, cost=fee),
            clientOrderId=params["clientOrderId"],
        )
        c.orders[row["id"]] = row
        if c.unknown:
            raise TimeoutError("ACK lost")
        return row

    async def order(oid, symbol):
        return dict(c.orders[oid])

    async def history(symbol):
        return list(c.orders.values())

    c.fetch_order_book, c.fetch_balance, c.fetch_open_orders = (
        book,
        balance,
        open_orders,
    )
    c.fetch_positions, c.fetch_trading_fee, c.fetch_position_mode = positions, fee, mode
    c.fetch_funding_history, c.create_order, c.fetch_order, c.fetch_orders = (
        funding_history,
        create,
        order,
        history,
    )
    return c


async def setup(tmp_path):
    clock, state = Clock(), {"spot": 2.0, "future": 0.0}
    s, f = exchange(clock, True, state), exchange(clock, False, state)

    async def funding(v, symbol):
        return NS(
            exchange=v,
            symbol=symbol,
            rate=0.001,
            next_ts=(clock() + 3600) * 1000,
            interval_hours=8,
        )

    a = Admission({"binance": s}, {"binance": f}, NS(get=funding), clock=clock)
    d = Diary(str(tmp_path / "d.sqlite"))
    durable = Store(d.path)
    await d.init()
    await durable.init()
    await Results(d.path).init()
    gates = {"entry": True, "exit": True}
    halted = []
    session = Session(
        durable,
        d,
        a,
        lambda v: gates["entry"],
        lambda v: gates["exit"],
        halted.append,
        clock,
    )
    op = dict(
        exchange="binance",
        base="X",
        direction="LONG_SPOT_SHORT_FUTURE",
        spot_symbol=SS,
        future_symbol=FS,
    )
    return session, op, s, f, state, clock, gates, halted


def test_account_preview_is_read_only_and_rejects_borrowing(tmp_path):
    async def go():
        x, op, s, f, *_ = await setup(tmp_path)
        p = await x.admission.preview(op)
        assert p["status"] == "ACCOUNT_PREFLIGHT_OK", p
        assert not p["release_authorized"] and not s.sent and not f.sent
        result = await x.admission.preview(dict(op, direction="LONG_FUTURE_SHORT_SPOT"))
        assert result["reason"] == "SPOT_BORROWING_UNVERIFIED"
        assert not await x.store.active()

    asyncio.run(go())


def test_real_adapter_entry_close_restart_and_atomic_dust_result(tmp_path):
    async def go():
        x, op, s, f, state, clock, gates, _ = await setup(tmp_path)
        result = await x.enter(op, "t")
        assert result["status"] == "OPEN", result
        assert f.sent[0][2] * 0.001 < s.sent[0][2]
        assert s.sent[0][3]["timeInForce"] == "IOC"
        assert "reduceOnly" not in s.sent[0][3]
        observed = await x.reconcile("t")
        assert observed["status"] == "PRIVATE_VERIFIED", observed
        assert not observed["replay_authorized"]
        assert (await x.enter(op, "t"))["status"] == "RECONCILE_REQUIRED"
        # Entry acceptance can expire while independent exit remains permitted.
        gates["entry"] = False
        f.sell_price, f.buy_price = 99.99, 100.01
        closed = await x.close("t")
        assert closed["status"] == "ACCOUNTING_PENDING", closed
        assert f.sent[1][3]["reduceOnly"] is True
        assert "reduceOnly" not in s.sent[1][3]
        assert state["future"] == 0 and state["spot"] >= 2
        assert (await x.finalize("t"))["status"] == "ACCOUNTING_PENDING"
        clock.now += 31
        final = await x.finalize("t")
        assert final["status"] == "CLOSED", final
        row = await x.store.get("t")
        assert row["phase"] == "CLOSED_WITH_INVENTORY"
        persisted = json.loads(row["payload"])["result"]
        assert (
            not persisted["private_flat"]
            and persisted["inventory_valuation_in_net"] == 0
        )
        assert persisted["held_inventory_cost"] < 0.05
        totals = await Results(x.diary.path).totals()
        assert totals["closed"] == 1 and totals["net"] > 0
        await x.finalize("t")
        assert (await Results(x.diary.path).totals())["closed"] == 1
        assert not await x.store.active()
        assert len(s.sent) == len(f.sent) == 2

    asyncio.run(go())


@pytest.mark.parametrize("leg", ["spot", "future"])
def test_unknown_ack_never_triggers_blind_second_leg_or_close(tmp_path, leg):
    async def go():
        x, op, s, f, _, _, _, halted = await setup(tmp_path)
        (s if leg == "spot" else f).unknown = True
        result = await x.enter(op, "t")
        assert result["status"] == "HOLD", result
        assert halted
        assert len(s.sent) == 1 and len(f.sent) == (0 if leg == "spot" else 1)
        before = len(s.sent), len(f.sent)
        observation = await x.reconcile("t")
        assert observation["status"] == "PRIVATE_VERIFIED", observation
        assert await x.store.active()
        assert (await x.enter(op, "t"))["status"] == "RECONCILE_REQUIRED"
        assert (len(s.sent), len(f.sent)) == before

    asyncio.run(go())


def test_terminal_zero_spot_fill_aborts_without_any_future_order(tmp_path):
    async def go():
        x, op, s, f, state, *_ = await setup(tmp_path)
        s.ratio = 0
        result = await x.enter(op, "t")
        assert result["status"] == "ABORTED", result
        assert state["spot"] == 2 and not f.sent and not await x.store.active()

    asyncio.run(go())


def test_terminal_zero_future_fill_protectively_sells_only_owned_spot(tmp_path):
    async def go():
        x, op, s, f, state, *_ = await setup(tmp_path)
        f.ratio = 0
        result = await x.enter(op, "t")
        assert result["status"] == "ACCOUNTING_PENDING", result
        assert len(s.sent) == 2 and len(f.sent) == 1
        assert s.sent[1][2] < s.sent[0][2]
        assert state["spot"] >= 2 and state["future"] == 0

    asyncio.run(go())


def test_global_capacity_blocks_second_strategy_or_process(tmp_path):
    async def go():
        x, op, s, f, *_ = await setup(tmp_path)
        await x.store.reserve_entry(
            "other",
            symbol=FS,
            long_venue="a",
            short_venue="b",
            planned_long=1,
            planned_short=1,
        )
        result = await x.enter(op, "t")
        assert result["reason"] == "GLOBAL_LIVE_CAPACITY"
        assert not s.sent and not f.sent

    asyncio.run(go())


def test_revoked_exit_cannot_sell_inventory_or_claim_flat(tmp_path):
    async def go():
        x, op, s, f, _, _, gates, halted = await setup(tmp_path)
        assert (await x.enter(op, "t"))["status"] == "OPEN"
        gates["exit"] = False
        result = await x.close("t")
        assert result["status"] == "HOLD" and halted
        assert len(s.sent) == len(f.sent) == 1
        assert (await x.store.get("t"))["phase"] == "CASH_HOLD"

    asyncio.run(go())


def test_missing_base_fee_currency_in_actual_fill_keeps_global_hold(tmp_path):
    async def go():
        x, op, s, f, _, _, _, halted = await setup(tmp_path)
        s.fee_currency = "BNB"
        result = await x.enter(op, "t")
        assert result["status"] == "HOLD" and halted and not f.sent
        assert (await x.reconcile("t"))["status"] == "HOLD"

    asyncio.run(go())


def test_partial_future_close_does_not_sell_spot_or_finalize(tmp_path):
    async def go():
        x, op, s, f, *_ = await setup(tmp_path)
        assert (await x.enter(op, "t"))["status"] == "OPEN"
        f.ratio = 0.5
        result = await x.close("t")
        assert (
            result["status"] == "HOLD"
            and result["reason"] == "CASH_FUTURE_EXIT_RESIDUAL"
        ), result
        assert len(s.sent) == 1 and len(f.sent) == 2
        assert (await Results(x.diary.path).totals())["closed"] == 0

    asyncio.run(go())


def test_two_concurrent_sessions_cannot_both_send_entry(tmp_path):
    async def go():
        x, op, s, f, *_ = await setup(tmp_path)
        other = Session(
            x.store,
            x.diary,
            x.admission,
            x.entry_authority,
            x.exit_authority,
            clock=x.clock,
        )
        results = await asyncio.gather(x.enter(op, "t1"), other.enter(op, "t2"))
        assert sum(r["status"] == "OPEN" for r in results) == 1, results
        assert len(s.sent) == len(f.sent) == 1

    asyncio.run(go())


def test_explicit_recovery_after_known_partial_close_has_distinct_intent_ids(tmp_path):
    async def go():
        x, op, s, f, state, clock, *_ = await setup(tmp_path)
        assert (await x.enter(op, "t"))["status"] == "OPEN"
        f.ratio = 0.4
        assert (await x.close("t"))["status"] == "HOLD"
        f.ratio = 1
        result = await x.recover("t")
        assert result["status"] == "ACCOUNTING_PENDING", result
        assert state["future"] == 0 and state["spot"] >= 2
        assert len({row[3]["clientOrderId"] for row in f.sent}) == 3
        clock.now += 31
        assert (await x.finalize("t"))["status"] == "CLOSED"

    asyncio.run(go())


def test_explicit_recovery_waits_for_unknown_order_lookup(tmp_path):
    async def go():
        x, op, s, f, *_ = await setup(tmp_path)
        f.unknown = True
        assert (await x.enter(op, "t"))["status"] == "HOLD"
        saved = f.orders.copy()
        f.orders.clear()
        before = len(s.sent), len(f.sent)
        assert (await x.recover("t"))["status"] == "HOLD"
        assert (len(s.sent), len(f.sent)) == before
        f.orders.update(saved)
        f.unknown = False
        result = await x.recover("t")
        assert result["status"] == "ACCOUNTING_PENDING", result

    asyncio.run(go())


def test_cash_recovery_is_bounded_and_does_not_consume_old_spot_inventory(tmp_path):
    async def go():
        x, op, s, f, state, *_ = await setup(tmp_path)
        assert (await x.enter(op, "t"))["status"] == "OPEN"
        s.ratio = 0
        assert (await x.close("t"))["status"] == "ACCOUNTING_PENDING"
        for _ in range(3):
            assert (await x.recover("t"))["status"] == "ACCOUNTING_PENDING"
        before = len(s.sent)
        result = await x.recover("t")
        assert result["reason"] == "CASH_RECOVERY_ROUND_LIMIT"
        assert len(s.sent) == before and state["spot"] >= 2
        assert (await Results(x.diary.path).totals())["closed"] == 0

    asyncio.run(go())


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("fee_scope", "CASH_FEE_SCOPE_MISMATCH"),
        ("mode", "CASH_ONE_WAY_REQUIRED"),
        ("funding_scope", "CASH_FUNDING_SCOPE_MISMATCH"),
        ("funding_rate", "CASH_FUNDING_UNKNOWN"),
        ("funding_calendar", "CASH_FUNDING_CALENDAR_UNKNOWN"),
        ("capability", "CASH_ACCOUNT_CAPABILITY_UNVERIFIED"),
        ("position", "CASH_ACCOUNT_NOT_IDLE"),
        ("wallet", "CASH_SHARED_WALLET_BUFFER_LOW"),
        ("book", "PUBLIC_BOOK_CROSSED"),
        ("net", "CASH_NET_BELOW_MINIMUM"),
    ],
)
def test_account_admission_rejects_unverified_or_uneconomic_routes_without_writes(
    tmp_path, fault, reason
):
    async def go():
        x, op, s, f, state, clock, *_ = await setup(tmp_path)
        if fault == "fee_scope":

            async def fee(symbol):
                return dict(symbol="other", maker=0.001, taker=0.001)

            s.fetch_trading_fee = fee
        elif fault == "mode":

            async def mode(symbol):
                return dict(hedged=True)

            f.fetch_position_mode = mode
        elif fault.startswith("funding"):

            async def fund(v, symbol):
                return NS(
                    exchange="other" if fault == "funding_scope" else v,
                    symbol=symbol,
                    rate=True if fault == "funding_rate" else 0.001,
                    next_ts=(
                        clock() - 3600
                        if fault == "funding_calendar"
                        else clock() + 3600
                    )
                    * 1000,
                    interval_hours=8,
                )

            x.admission.funding.get = fund
        elif fault == "capability":
            f.has["fetchFundingHistory"] = False
        elif fault == "position":
            state["future"] = 1
        elif fault == "wallet":

            async def balance():
                return {
                    "X": dict(free=2, used=0, total=2),
                    "USDT": dict(free=10, used=0, total=10),
                }

            s.fetch_balance = balance
        elif fault == "book":
            s.sell_price = 101
        elif fault == "net":
            f.sell_price, f.buy_price = 100, 100.01
            x.admission.notional = 4.95
        result = await x.admission.preview(op)
        assert result["reason"] == reason, result
        assert not s.sent and not f.sent and not await x.store.active()

    asyncio.run(go())


def test_verified_result_is_exactly_once_across_two_database_connections(tmp_path):
    async def go():
        x, op, _, f, _, clock, *_ = await setup(tmp_path)
        await x.enter(op, "t")
        f.sell_price, f.buy_price = 99.99, 100.01
        await x.close("t")
        row = await x.store.get("t")
        meta = json.loads(row["payload"])
        p = NS(
            **meta["cash_plan"],
            persisted=meta["cash_plan"],
            closed_at=meta["cash_closed_at"],
            reason="OPERATOR_EXIT"
        )
        flow = await x._flow("t", p)
        proof = await x._private(p, flow)
        funding = Evidence(True, 0, (), "VERIFIED", p.closed_at)
        results = await asyncio.gather(
            *(
                finalize(x.diary.path, "t", p, flow, proof, funding, clock())
                for _ in range(2)
            )
        )
        assert sorted(results) == [False, True]
        assert (await Results(x.diary.path).totals())["closed"] == 1

    asyncio.run(go())


@pytest.mark.parametrize(
    "fault",
    [
        "immature",
        "event_scope",
        "duplicate_event",
        "wrong_total",
        "private_flat",
        "journal_changed",
    ],
)
def test_final_result_invalid_proof_rolls_back_without_releasing_capacity(
    tmp_path, fault
):
    async def go():
        x, op, _, f, _, clock, *_ = await setup(tmp_path)
        await x.enter(op, "t")
        f.sell_price, f.buy_price = 99.99, 100.01
        await x.close("t")
        row = await x.store.get("t")
        meta = json.loads(row["payload"])
        p = NS(
            **meta["cash_plan"],
            persisted=meta["cash_plan"],
            closed_at=meta["cash_closed_at"],
            reason="OPERATOR_EXIT"
        )
        flow = await x._flow("t", p)
        proof = await x._private(p, flow)
        event = dict(
            venue="other" if fault == "event_scope" else "binance",
            event_id="fund",
            symbol=FS,
            ts=p.opened_at,
            amount=0.01,
        )
        evidence = Evidence(
            fault != "immature",
            0.02 if fault == "wrong_total" else 0.01,
            (event, event) if fault == "duplicate_event" else (event,),
            "VERIFIED",
            p.closed_at,
        )
        if fault == "private_flat":
            proof["future_flat"] = False
        if fault == "journal_changed":
            from dataclasses import replace

            flow = replace(flow, quote_fees=flow.quote_fees + 1)
        with pytest.raises(ValueError):
            await finalize(x.diary.path, "t", p, flow, proof, evidence, clock())
        assert (await Results(x.diary.path).totals())["closed"] == 0
        assert (await x.store.get("t"))["phase"] == "CASH_ACCOUNTING_PENDING"
        assert await x.store.active()

    asyncio.run(go())
