import asyncio
import json
from types import SimpleNamespace as NS
import pytest
from app.live_spot_future_dispatch import Coordinator
from app.live_monitor import Monitor
from app.live_supervisor import LiveSupervisor
from app.private_registry import PrivateRegistry
from app.private_reader import PrivateReader
from app.private_order_reader import Reader as FutureReader
from app.spot_executor import SpotOrderReader
from app.runtime_store import RuntimeStore
from app.persistent_stop import Stop
from app.live_acceptance import (
    CHECKS,
    VENUE_CHECKS,
    SPOT_CHECKS,
    accepted,
    spot_accepted,
)
from app.live_commands import clear_monitor_kill
from app.tg_spot_future_live import render, menu
from tests.test_spot_future_live_session import setup, SS, FS


async def monitor_setup(tmp_path, automatic=False):
    x, op, s, f, state, clock, gates, halted = await setup(tmp_path)
    c = Coordinator(x, clock=clock)
    registry = PrivateRegistry()
    registry.add("binance", PrivateReader("binance", f))

    async def snapshot():
        rows = await registry.snapshot()
        for r in rows.values():
            r["snapshot_started_at"] = r["fetched_at"] = clock()
        return rows

    async def update(summary, incidents):
        summary["closed"].extend(await c.process(summary))

    supervisor = LiveSupervisor(3)
    stop = Stop(str(tmp_path / "stop.json"))
    m = Monitor(
        x.store,
        RuntimeStore(str(tmp_path / "runtime.json")),
        x.diary,
        snapshot,
        {
            "binance": FutureReader("binance", f),
            "binance:spot": SpotOrderReader("binance:spot", s),
        },
        supervisor,
        stop,
        cash_observer=c,
        clock=clock,
        on_update=update if automatic else None,
    )
    await m.init()
    return x, op, s, f, state, clock, gates, halted, c, m


def test_dispatch_scanner_row_enters_shared_durable_lifecycle(tmp_path):
    async def go():
        x, op, s, f, _, _, _, _, c, _ = await monitor_setup(tmp_path)
        result = await c.process_rows([dict(op, hypothetical_edge=3)])
        assert result["status"] == "OPEN", result
        row = (await x.store.active())[0]
        assert json.loads(row["payload"])["strategy"] == "spot_futures"
        assert row["trade_id"].startswith("sf-")
        assert len(s.sent) == len(f.sent) == 1
        assert (await c.process_rows([op]))["status"] == "GLOBAL_LIVE_CAPACITY"

    asyncio.run(go())


def test_common_monitor_owns_cash_future_without_false_unmanaged_or_ff_rebuild(
    tmp_path,
):
    async def go():
        x, op, s, f, _, _, _, _, _, m = await monitor_setup(tmp_path)
        await x.enter(op, "t")
        before = len(s.sent), len(f.sent)
        summary = await m.cycle()
        assert summary["private_verified"] and summary["reconciled"], summary[
            "incidents"
        ]
        assert not summary["runtime_trades"]
        assert summary["trades"][0]["strategy"] == "spot_futures"
        assert summary["trades"][0]["private_verified"]
        assert not any(
            i["code"]
            in (
                "UNMANAGED_POSITION",
                "ORDER_SCOPE_MISMATCH",
                "PRIVATE_SNAPSHOT_UNTRUSTED",
            )
            for i in summary["incidents"]
        )
        assert (len(s.sent), len(f.sent)) == before
        assert (await x.store.get("t"))["phase"] == "CASH_OPEN"

    asyncio.run(go())


def test_no_registered_cash_monitor_fails_closed_without_reinterpreting_leg_symbols(
    tmp_path,
):
    async def go():
        x, op, _, _, _, _, _, _, _, m = await monitor_setup(tmp_path)
        await x.enter(op, "t")
        m.cash_observer = None
        summary = await m.cycle()
        assert not summary["reconciled"]
        assert any(i["code"] == "CASH_PRIVATE_UNVERIFIED" for i in summary["incidents"])
        assert (await x.store.get("t"))["phase"] == "CASH_OPEN"

    asyncio.run(go())


def test_private_spot_change_blocks_common_resume_proof_and_automatic_exit(tmp_path):
    async def go():
        x, op, s, f, state, _, _, _, _, m = await monitor_setup(
            tmp_path, automatic=True
        )
        await x.enter(op, "t")
        state["spot"] -= 0.01
        before = len(s.sent), len(f.sent)
        summary = await m.cycle()
        assert not summary["private_verified"] and not summary["reconciled"]
        assert m.stop.stopped and m.supervisor.kill.global_reason
        assert (len(s.sent), len(f.sent)) == before

    asyncio.run(go())


def test_common_monitor_update_exits_and_finalizes_cash_once_after_funding_maturity(
    tmp_path,
):
    async def go():
        x, op, s, f, state, clock, _, _, _, m = await monitor_setup(
            tmp_path, automatic=True
        )
        await x.enter(op, "t")
        clock.now += 31
        f.sell_price, f.buy_price = 99.99, 100.01
        observed = await m.cycle()
        info = observed["trades"][0]
        assert info["funding_known"] and info["exit_signal"] == "TARGET_CAPTURE", info
        assert (await x.store.get("t"))["phase"] == "CASH_ACCOUNTING_PENDING"
        assert state["future"] == 0
        clock.now += 31
        summary = await m.cycle()
        assert len(summary["closed"]) == 1
        assert summary["closed"][0]["strategy"] == "spot_futures"
        later = await m.cycle()
        assert not later["trades"] and not later["closed"]
        assert later["cash_inventory"] and later["realized"]["closed"] == 1
        assert len(s.sent) == len(f.sent) == 2

    asyncio.run(go())


def test_time_stop_requires_no_fabricated_funding_profit(tmp_path):
    async def go():
        x, op, _, f, _, clock, _, _, c, _ = await monitor_setup(tmp_path)
        await x.enter(op, "t")
        c.max_seconds = 1
        clock.now += 2
        f.has["fetchFundingHistory"] = False
        info = await c.observe(await x.store.get("t"))
        assert info["private_verified"] and info["exit_signal"] == "TIME_STOP"
        assert not info["funding_known"]
        assert not info["funding"]

    asyncio.run(go())


def test_immature_funding_does_not_trigger_profitable_target_exit(tmp_path):
    async def go():
        x, op, s, f, _, _, _, _, c, _ = await monitor_setup(tmp_path)
        await x.enter(op, "t")
        f.sell_price, f.buy_price = 99.99, 100.01
        before = len(s.sent), len(f.sent)
        info = await c.observe(await x.store.get("t"))
        assert info["estimated_net"] > 0 and not info["funding_known"]
        assert info["exit_signal"] == "HOLD"
        assert (len(s.sent), len(f.sent)) == before

    asyncio.run(go())


def test_general_ff_dispatch_skips_cash_positions(tmp_path):
    from app.live_exit_dispatch import Coordinator as FF

    async def go():
        x, op, _, _, _, _, _, _, c, _ = await monitor_setup(tmp_path)
        await x.enter(op, "t")
        info = await c.observe(await x.store.get("t"))
        info["exit_signal"] = "TIME_STOP"
        ff = FF(x.store, {}, None, lambda _: True, NS(stopped=False))
        assert not await ff.process(dict(trades=[info], runtime_trades=[]))

    asyncio.run(go())


def evidence(now=1000):
    return dict(
        version=1,
        evidence_id="account/run",
        verified_at=now - 1,
        expires_at=now + 10,
        checks={k: True for k in CHECKS},
        venues={"binance": {k: True for k in VENUE_CHECKS}},
        strategies={
            "spot_futures": dict(venues={"binance": {k: True for k in SPOT_CHECKS}})
        },
    )


@pytest.mark.parametrize("missing", SPOT_CHECKS)
def test_futures_acceptance_never_authorizes_missing_cash_account_check(
    tmp_path, missing
):
    p = tmp_path / "acceptance.json"
    d = evidence()
    d["strategies"]["spot_futures"]["venues"]["binance"][missing] = False
    p.write_text(json.dumps(d))
    assert accepted(p, ("binance",), 1000)
    assert not spot_accepted(p, "binance", 1000)


def test_cash_acceptance_scope_and_expiry_are_explicit(tmp_path):
    p = tmp_path / "acceptance.json"
    p.write_text(json.dumps(evidence()))
    assert spot_accepted(p, "binance", 1000)
    assert not spot_accepted(p, "bybit", 1000)
    assert not spot_accepted(p, "binance", 1011)


@pytest.mark.parametrize(
    "fault", ["stale", "unknown", "private", "incident", "foreign_kill"]
)
def test_manual_monitor_kill_clear_needs_current_clean_proof(fault):
    s = LiveSupervisor(3)
    s.private_verified, s.restart_clean, s.unknown_orders = True, True, False
    s.kill.trip_global(
        "OPERATOR_LOCK" if fault == "foreign_kill" else "LIVE_MONITOR:UNKNOWN"
    )
    summary = dict(
        ts=999, private_verified=True, reconciled=True, unknown_orders=0, incidents=[]
    )
    if fault == "stale":
        summary["ts"] = 900
    elif fault == "unknown":
        summary["unknown_orders"] = 1
    elif fault == "private":
        summary["private_verified"] = False
    elif fault == "incident":
        summary["incidents"] = [dict(severity="HIGH")]
    assert not clear_monitor_kill(s, summary, 1000).allowed
    assert s.kill.global_reason


def test_manual_monitor_kill_clear_never_resumes_stop(tmp_path):
    s = LiveSupervisor(3)
    s.private_verified, s.restart_clean, s.unknown_orders = True, True, False
    s.kill.trip_global("LIVE_MONITOR:UNRESOLVED_ORDER")
    stop = Stop(str(tmp_path / "stop"))
    summary = dict(
        ts=999, private_verified=True, reconciled=True, unknown_orders=0, incidents=[]
    )
    assert clear_monitor_kill(s, summary, 1000).allowed
    assert not s.kill.global_reason and stop.stopped


def test_cash_controls_and_inventory_labels_do_not_claim_flat():
    summary = dict(
        trades=[
            dict(
                strategy="spot_futures",
                trade_id="sf-a",
                symbol=FS,
                spot_symbol=SS,
                phase="CASH_OPEN",
                private_verified=True,
            )
        ],
        cash_inventory=[dict(venue="binance", base="X", qty=0.0001, cost_usd=0.01)],
    )
    markup = menu(summary)
    assert any(
        b.callback_data == "sf_close:sf-a" and b.style == "danger"
        for row in markup.inline_keyboard
        for b in row
    )
    assert "не flat" in render(summary, None)
    summary["trades"][0]["phase"] = "CASH_HOLD"
    assert any(
        b.callback_data == "sf_recover:sf-a"
        for row in menu(summary).inline_keyboard
        for b in row
    )


def test_private_spot_factory_has_separate_clients_and_native_cash_scope(monkeypatch):
    import app.private_factory as factory

    made = []
    monkeypatch.setattr(factory, "configured", lambda: ["binance"])
    monkeypatch.setattr(
        factory,
        "credentials",
        lambda _: dict(apiKey="offline", secret="offline", password=""),
    )

    def build(params):
        c = NS(options=params["options"], params=params)
        made.append(c)
        return c

    monkeypatch.setattr(factory, "exchange_class", lambda _: build)
    cash = factory.build_spot_clients()
    _, derivatives = factory.build_private_readers()
    assert cash["binance"] is not derivatives["binance"]
    assert cash["binance"].options["defaultType"] == "spot"
    assert derivatives["binance"].options["defaultType"] == "swap"


def test_secondary_rows_hook_receives_persisted_rows_and_entry_state():
    from app.secondary_strategy_runtime import SecondaryRuntime
    from app.strategy_runtime import StrategyRuntime

    async def go():
        called = []

        async def record(path, rows):
            called.append("persist")

        async def cycle():
            return [dict(strategy="spot_futures", base="X", hypothetical_edge=3)]

        sr = SecondaryRuntime(StrategyRuntime(), record, "offline", interval=0)
        sr.running = True

        async def on_rows(name, rows, enabled):
            called.append((name, rows[0]["base"], enabled))
            sr.running = False

        sr.on_rows = on_rows
        await sr._loop("spot_futures", NS(cycle=cycle, paper=True))
        assert called == ["persist", ("spot_futures", "X", True)]

    asyncio.run(go())


def test_revoked_entry_authority_uses_independent_protective_spot_exit(tmp_path):
    async def go():
        x, op, s, f, state, _, gates, *_ = await setup(tmp_path)
        original = s.create_order

        async def create(*args):
            result = await original(*args)
            if args[2] == "buy":
                gates["entry"] = False
            return result

        s.create_order = create
        result = await x.enter(op, "t")
        assert result["status"] == "ACCOUNTING_PENDING", result
        assert not f.sent and len(s.sent) == 2 and state["spot"] >= 2

    asyncio.run(go())


def test_recent_unsettled_calendar_event_blocks_target_credit(tmp_path):
    async def go():
        x, op, _, f, _, clock, _, _, c, _ = await monitor_setup(tmp_path)
        await x.enter(op, "t")
        clock.now += 31

        async def funding(v, symbol):
            return NS(
                exchange=v,
                symbol=symbol,
                rate=0.001,
                next_ts=int((clock() + 8 * 3600 - 10) * 1000),
                interval_hours=8,
            )

        x.admission.funding.get = funding
        f.sell_price, f.buy_price = 99.99, 100.01
        info = await c.observe(await x.store.get("t"))
        assert info["estimated_net"] > 0 and not info["funding_known"]
        assert info["exit_signal"] == "HOLD"

    asyncio.run(go())


def test_mark_observer_cannot_reopen_concurrently_claimed_cash_exit(tmp_path):
    async def go():
        x, op, _, _, _, clock, _, _, c, _ = await monitor_setup(tmp_path)
        await x.enter(op, "t")
        info = await c.observe(await x.store.get("t"))
        await x.close("t")
        before = await x.store.get("t")
        await c.persist_mark(info)
        assert await x.store.get("t") == before

    asyncio.run(go())


def test_cash_write_dispatch_refuses_untrusted_common_summary(tmp_path):
    async def go():
        x, op, s, f, _, _, _, _, c, _ = await monitor_setup(tmp_path)
        await x.enter(op, "t")
        info = await c.observe(await x.store.get("t"))
        info["exit_signal"] = "TIME_STOP"
        before = len(s.sent), len(f.sent)
        assert not await c.process(
            dict(trades=[info], private_verified=False, reconciled=True)
        )
        assert (len(s.sent), len(f.sent)) == before

    asyncio.run(go())


def test_inventory_balance_gap_blocks_next_cash_entry(tmp_path):
    async def go():
        x, op, s, f, state, clock, _, _, c, _ = await monitor_setup(tmp_path)
        await x.enter(op, "t")
        await x.close("t")
        clock.now += 31
        assert (await x.finalize("t"))["status"] == "CLOSED"
        assert (await c.inventory())[0]["balance_covered"]
        state["spot"] = 0
        before = len(s.sent), len(f.sent)
        assert (await c.process_rows([op]))[
            "status"
        ] == "CASH_INVENTORY_BALANCE_UNVERIFIED"
        assert (len(s.sent), len(f.sent)) == before

    asyncio.run(go())


def test_strategy_policies_require_explicit_cash_scope_in_addition_to_standard_acceptance():
    from app.strategy_mode_policy import can_real
    from app.strategy_release_gate import evaluate

    assert not can_real("spot_futures", True, True)
    assert can_real("spot_futures", True, True, cash_accepted=True)
    assert not can_real("cex_dex", True, True, cash_accepted=True)
    assert not evaluate("spot_futures", True, True).live
    assert evaluate("spot_futures", True, True, cash_account_acceptance=True).live
