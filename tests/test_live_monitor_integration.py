import asyncio
import json
from types import SimpleNamespace as NS

import pytest
from app.db import Diary
from app.live_trade_store import Store
from app.live_monitor import Monitor
from app.live_supervisor import LiveSupervisor
from app.runtime_store import RuntimeStore
from app.runtime_state import RuntimeTrade
from app.persistent_stop import Stop
from app.live_order_intent import OrderIntent
from app.exchange_executor import SubmitResult
from app.private_funding_reader import Evidence

SYMBOL = "X/USDT:USDT"


def trade():
    return RuntimeTrade(
        "t", SYMBOL, "a", "b", 2, 4, 2, 0.5, 1, 100, 105, 900, entry_fees=0.4
    )


def snapshot(flat=False, stale=False):
    return {
        v: {
            "health": NS(ok=True),
            "snapshot_started_at": 900 if stale else 999,
            "fetched_at": 1000,
            "positions": [] if flat else [NS(symbol=SYMBOL, side=side, qty=2)],
            "orders": [],
        }
        for v, side in [("a", "long"), ("b", "short")]
    }


class Funding:
    verified = True

    async def collect(self, t, until):
        return Evidence(
            self.verified,
            0.3,
            (),
            "VERIFIED" if self.verified else "PENDING",
            until if self.verified else until - 30,
        )


class Market:
    max_age = 1.5

    async def mark(self, t):
        return dict(
            ok=True,
            long_exit=102,
            short_exit=103,
            exit_fee=0.2,
            fees_verified=True,
            ts=1000,
            spread=1,
        )


async def setup(
    tmp_path, state="UNKNOWN", canonical=False, flat=False, stale=False, readers=None
):
    d = Diary(str(tmp_path / "d.db"))
    await d.init()
    durable = Store(d.path)
    await durable.init()
    await durable.phase(
        "t",
        state,
        symbol=SYMBOL,
        long_venue="a",
        short_venue="b",
        long_contract_size=0.5,
        short_contract_size=1,
        opened_at=900,
        **({"runtime_trade": trade().row()} if canonical else {})
    )
    snap = snapshot(flat, stale)

    async def source():
        return snap

    m = Monitor(
        durable,
        RuntimeStore(str(tmp_path / "runtime.json")),
        d,
        source,
        readers or {},
        LiveSupervisor(),
        Stop(tmp_path / "stop.json"),
        Market(),
        Funding(),
        clock=lambda: 1000,
    )
    await m.init()
    return m, snap


async def fill(m, i, venue, side, qty, price, reduce=False, fee=0.1, state="FILLED"):
    intent = OrderIntent(i, "t", venue, SYMBOL, side, qty, reduce)
    await m.diary.save_order_intent_result(
        intent, state, SubmitResult(i, state, qty, price, fee)
    )


async def entries(m):
    await fill(m, "l", "a", "buy", 4, 100, fee=0.2)
    await fill(m, "s", "b", "sell", 2, 105, fee=0.2)


def test_restart_rebuilds_from_actual_fills_and_private_contract_exposure(tmp_path):
    async def go():
        m, _ = await setup(tmp_path)
        await entries(m)
        summary = await m.cycle()
        assert summary["reconciled"] and summary["marks"][0][
            "estimated_net"
        ] == pytest.approx(7.7)
        assert m.runtime.load()[0].long_contracts == 4
        row = await m.durable.get("t")
        assert row["phase"] == "OPEN"
        assert json.loads(row["payload"])["runtime_trade"]["entry_fees"] == 0.4
        assert m.stop.stopped and not summary["release_authorized"]
        again = await m.cycle()
        assert again["reconciled"] and len(m.runtime.load()) == 1

    asyncio.run(go())


def test_preserved_entry_hold_latches_stop_without_hiding_exposure(tmp_path):
    async def go():
        m, _ = await setup(tmp_path)
        await entries(m)
        await m.durable.phase(
            "t", "UNKNOWN", entry_hold_reason="FALLBACK_ACTUAL_SLIPPAGE_STOP"
        )
        summary = await m.cycle()
        assert m.stop.stopped and not summary["release_authorized"]
        assert len(m.runtime.load()) == 1
        assert any(
            x["code"] == "FALLBACK_ACTUAL_SLIPPAGE_STOP" for x in summary["incidents"]
        )

    asyncio.run(go())


@pytest.mark.parametrize(
    "case",
    [
        "unknown",
        "stale",
        "missing_fee",
        "half_leg",
        "wrong_direction",
        "unmanaged",
        "duplicate",
        "bad_runtime",
    ],
)
def test_uncertain_evidence_never_reconstructs_or_unlocks(tmp_path, case):
    async def go():
        m, snap = await setup(
            tmp_path, stale=case == "stale", canonical=case == "bad_runtime"
        )
        await entries(m)
        if case == "unknown":
            await m.diary.save_order_intent(
                OrderIntent("u", "t", "a", SYMBOL, "buy", 1, False), "UNKNOWN"
            )
        if case == "missing_fee":
            await fill(m, "l", "a", "buy", 4, 100, fee=None)
        if case == "half_leg":
            snap["b"]["positions"] = []
        if case == "wrong_direction":
            await fill(m, "wrong", "a", "sell", 1, 100)
        if case == "unmanaged":
            snap["a"]["positions"].append(NS(symbol="OTHER", side="long", qty=1))
        if case == "duplicate":
            await m.durable.phase(
                "other", "PLANNED", symbol=SYMBOL, long_venue="a", short_venue="b"
            )
        if case == "bad_runtime":
            bad = trade().row()
            bad["base_qty"] = float("nan")
            await m.durable.phase("t", "UNKNOWN", runtime_trade=bad)
        result = await m.cycle()
        assert not result["reconciled"] and m.stop.stopped
        assert result["incidents"] and not result["closed"]
        assert not m.supervisor.readiness().ready

    asyncio.run(go())


def test_closed_pnl_waits_for_funding_then_finalizes_once_and_clears_cache(tmp_path):
    async def go():
        m, _ = await setup(tmp_path, state="EXIT_SUBMITTING", canonical=True, flat=True)
        await entries(m)
        await fill(m, "cl", "a", "sell", 4, 102, True)
        await fill(m, "cs", "b", "buy", 2, 103, True)
        m.runtime.save([trade()])
        m.funding.verified = False
        pending = await m.cycle()
        assert not pending["closed"] and pending["trades"][0]["private_flat"]
        assert (await m.durable.get("t"))["phase"] == "EXIT_SUBMITTING"
        m.funding.verified = True
        result = await m.cycle()
        assert result["closed"][0]["net"] == pytest.approx(7.7)
        assert (await m.durable.get("t"))["phase"] == "CLOSED_PRIVATE_VERIFIED"
        assert not m.runtime.load()
        again = await m.cycle()
        assert not again["closed"]
        assert again["realized"]["closed"] == 1 and again["realized"][
            "net"
        ] == pytest.approx(7.7)
        assert not await m.store.finalize(
            "t",
            result["closed"][0],
            dict(private_flat=True, orders_terminal=True, funding_verified=True),
        )

    asyncio.run(go())


def test_private_flat_does_not_prove_unknown_order_or_missing_exit_fills(tmp_path):
    async def go():
        m, _ = await setup(tmp_path, canonical=True, flat=True)
        await entries(m)
        result = await m.cycle()
        assert not result["closed"] and any(
            x["code"] == "EXIT_FILL_QUANTITY_MISMATCH" for x in result["incidents"]
        )
        await m.diary.save_order_intent(
            OrderIntent("u", "t", "a", SYMBOL, "sell", 4, True), "UNKNOWN"
        )
        result = await m.cycle()
        assert result["unknown_orders"] == 1 and not result["closed"]
        assert (await m.durable.get("t"))["phase"] == "UNKNOWN"

    asyncio.run(go())


def test_missing_fee_cannot_be_inferred_from_known_terminal_status(tmp_path):
    async def go():
        m, _ = await setup(tmp_path, state="EXIT_SUBMITTING", canonical=True, flat=True)
        await entries(m)
        await fill(m, "cl", "a", "sell", 4, 102, True, fee=None)
        await fill(m, "cs", "b", "buy", 2, 103, True)
        result = await m.cycle()
        assert not result["closed"]
        assert any(x["code"] == "FILL_ACCOUNTING_MISSING" for x in result["incidents"])

    asyncio.run(go())


def test_exit_residual_is_observed_without_exchange_writes(tmp_path):
    async def go():
        m, _ = await setup(tmp_path, state="EXIT_SUBMITTING", canonical=True)
        await entries(m)
        result = await m.cycle()
        assert any(
            x["code"] == "EXIT_RESIDUAL_REQUIRES_RECOVERY" for x in result["incidents"]
        )
        assert (await m.durable.get("t"))["phase"] == "EXIT_SUBMITTING"

    asyncio.run(go())


def test_funding_event_conflicts_do_not_double_credit(tmp_path):
    async def go():
        m, _ = await setup(tmp_path)
        event = dict(venue="a", event_id="1", symbol=SYMBOL, ts=950, amount=0.3)
        await m.store.settlements("t", [event])
        await m.store.settlements("t", [event])
        with pytest.raises(ValueError, match="FUNDING_ATTRIBUTION_CONFLICT"):
            await m.store.settlements("other", [event])
        with pytest.raises(ValueError, match="CLOSE_PROOF_INCOMPLETE"):
            await m.store.finalize("t", {}, {})

    asyncio.run(go())


def test_incident_notification_is_deduplicated_and_stop_stays_latched(tmp_path):
    async def go():
        m, snap = await setup(tmp_path)
        await entries(m)
        snap["a"]["positions"].append(NS(symbol="OTHER", side="long", qty=1))
        notifications = []

        async def notify(summary, new):
            notifications.extend(new)

        m.on_update = notify
        await m.cycle()
        await m.cycle()
        assert len([x for x in notifications if x["code"] == "UNMANAGED_POSITION"]) == 1
        snap["a"]["positions"].pop()
        result = await m.cycle()
        assert result["reconciled"] and m.stop.stopped
        assert m.supervisor.kill.check("", "", "").blocked

    asyncio.run(go())


def test_background_failure_replaces_old_health_and_shuts_down_cleanly(tmp_path):
    async def go():
        m, _ = await setup(tmp_path)

        async def failing():
            raise RuntimeError("offline")

        m.snapshot_source = failing
        m.latest = dict(private_verified=True, reconciled=True)
        await m.start()
        for _ in range(100):
            if m.latest.get("private_verified") is False:
                break
            await asyncio.sleep(0.005)
        await m.stop_task()
        assert m.task is None and not m.latest["private_verified"]
        assert not (await m.store.latest())["reconciled"]
        assert m.stop.reason == "LIVE_MONITOR_FAILURE"
        from app.live_monitor_view import status

        assert "не подтверждён" in status(m.latest)

    asyncio.run(go())
