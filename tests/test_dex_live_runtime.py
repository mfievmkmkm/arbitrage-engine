"""Offline runtime integration: journals and generated evidence, no network or keys."""

import asyncio
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace as NS
import pytest
from app.dex_live_runtime import Coordinator, Marker, Source
from app.dex_live_bootstrap import build, enabled
from test_dex_live_bridge import setup, envelope, NOW


def summary(tid, phase="DEX_WALLET_PENDING", signal="HOLD"):
    return dict(
        ts=NOW,
        reconciled=True,
        private_verified=True,
        unknown_orders=0,
        trades=[
            dict(
                trade_id=tid,
                strategy="cex_dex",
                private_verified=True,
                phase=phase,
                exit_signal=signal,
            )
        ],
    )


def coordinator(s):
    return Coordinator(s, NS(), NS())


def test_current_entry_hedges_once_then_restart_unwinds_old_entry(tmp_path):
    async def run():
        s, p, w, c = await setup(tmp_path)
        await s.enter("t", p, envelope(p))
        co = coordinator(s)
        co.created.add("t")
        await co.process(summary("t"))
        await co.process(summary("t"))
        assert c.calls == ["hedge"]
        assert (await s.store.get("t"))["phase"] == "DEX_OPEN"

    asyncio.run(run())


def test_restart_unhedged_entry_never_opens_cex_position(tmp_path):
    async def run():
        s, p, w, c = await setup(tmp_path)
        await s.enter("t", p, envelope(p))
        co = coordinator(s)
        await co.process(summary("t"))
        assert not c.calls and len(w.calls) == 2
        assert (await s.store.get("t"))["phase"] == "DEX_ACCOUNTING_PENDING"
        closed = await co.process(summary("t"))
        assert len(closed) == 1 and closed[0]["status"] == "CLOSED"
        assert not await s.store.active()

    asyncio.run(run())


@pytest.mark.parametrize("fault", ["unknown", "stale", "private", "reconcile"])
def test_uncertain_state_never_hedges_or_repeats_wallet_send(tmp_path, fault):
    async def run():
        s, p, w, c = await setup(tmp_path)
        w.unknown = fault == "unknown"
        await s.enter("t", p, envelope(p))
        co = coordinator(s)
        co.created.add("t")
        report = summary("t")
        if fault == "stale":
            report["ts"] -= 16
        if fault == "private":
            report["private_verified"] = False
        if fault == "reconcile":
            report["reconciled"] = False
        await co.process(report)
        await co.process(report)
        assert len(w.calls) == 1 and not c.calls
        assert await s.store.active()

    asyncio.run(run())


def test_profitable_exit_is_rechecked_before_sending(tmp_path):
    async def run():
        s, p, w, c = await setup(tmp_path)
        await s.enter("t", p, envelope(p))
        await s.hedge("t")
        co = coordinator(s)

        async def fresh(row):
            return dict(private_verified=True, exit_signal="HOLD")

        co.marker.observe = fresh
        await co.process(summary("t", "DEX_OPEN", "TARGET_CAPTURE"))
        assert c.calls == ["hedge"] and len(w.calls) == 1
        assert (await s.store.get("t"))["phase"] == "DEX_OPEN"

        async def stop(row):
            return dict(private_verified=True, exit_signal="NET_STOP")

        co.marker.observe = stop
        await co.process(summary("t", "DEX_OPEN", "TARGET_CAPTURE"))
        assert c.calls == ["hedge", "exit"] and len(w.calls) == 2

    asyncio.run(run())


@pytest.mark.parametrize(
    "funding,price,signal",
    [
        (True, "4", "TARGET_CAPTURE"),
        (False, "4", "HOLD"),
        (False, "5", "NET_STOP"),
        ("error", "5", "NET_STOP"),
    ],
)
def test_paired_net_mark_and_unknown_funding_loss_stop(
    tmp_path, funding, price, signal
):
    async def run():
        s, p, w, c = await setup(tmp_path)
        await s.enter("t", p, envelope(p))
        await s.hedge("t")
        await s._change("t", ("DEX_OPEN",), "DEX_OPEN", dex_entry_edge="0.2")
        order = []

        async def reverse(plan, raw):
            e = envelope(plan, True, raw)
            e.proof.update(
                min_buy_amount_raw="410",
                max_sell_amount_raw="410",
                network_fee_raw="100",
                ts=NOW,
            )
            return e

        s.reverse_quote = reverse

        async def fee(symbol):
            order.append("fee")
            return dict(symbol=symbol, taker="0.001")

        c.clients = {p.venue: NS(fetch_trading_fee=fee)}
        original = c.prepare

        async def prepare(plan, raw, closing):
            order.append("cex_book")
            r = await original(plan, raw, closing)
            r.reference_price = Decimal(price)
            r.market_evidence = dict(book_ts=NOW)
            return r

        c.prepare = prepare

        async def gas(raw):
            order.append("gas")
            return Decimal("0.02"), dict(timestamp=NOW * 1000)

        m = Marker(s, NS(gas=gas), NS(), stop_net=-0.5)

        async def coverage(plan, now):
            order.append("funding")
            if funding == "error":
                raise TimeoutError()
            return dict(verified=funding, amount="0")

        m.coverage = coverage
        info = await m.observe(await s.store.get("t"))
        assert info["exit_signal"] == signal, info
        assert info["funding_known"] is (funding is True)
        assert order == ["fee", "funding", "gas", "cex_book"]
        assert not info.get("cash_market_error"), info

    asyncio.run(run())


def test_time_limit_still_exits_if_market_quote_unavailable(tmp_path):
    async def run():
        s, p, w, c = await setup(tmp_path)
        await s.enter("t", p, envelope(p))
        await s.hedge("t")
        m = Marker(s, NS(), NS(), max_seconds=1200)
        m.clock = lambda: NOW + 1200
        info = await m.observe(await s.store.get("t"))
        assert info["private_verified"] and info["exit_signal"] == "TIME_STOP"
        assert info.get("cash_market_error")

    asyncio.run(run())


def test_candidate_consumed_once_and_stale_candidate_removed(tmp_path):
    async def run():
        s, p, _, _ = await setup(tmp_path)
        source = Source(
            None,
            None,
            NS(),
            {},
            [dict(asset_token=p.asset, asset_amount_raw="100", direction="forward")],
            None,
            clock=lambda: NOW,
        )
        source.cache["one"] = (p, envelope(p))
        assert source.take("one")[0] == p
        with pytest.raises(ValueError, match="CONSUMED"):
            source.take("one")
        source.cache["old"] = (replace(p, opened_at=NOW - 16), envelope(p))
        with pytest.raises(ValueError, match="STALE"):
            source.take("old")
        assert not source.cache

    asyncio.run(run())


def test_never_claimed_reservation_aborts_without_creating_profit(tmp_path):
    async def run():
        s, p, w, c = await setup(tmp_path)

        async def blocked(*args, **kwargs):
            raise ValueError("SEND_GATE_CHANGED")

        w.send = blocked
        await s.enter("t", p, envelope(p))
        assert (await s.store.get("t"))["phase"] == "PLANNED"
        assert (await s.abort_reserved("t"))["status"] == "ABORTED"
        assert not await s.store.active()
        assert (await s.abort_reserved("t"))["status"] == "RECONCILE_REQUIRED"
        import aiosqlite

        async with aiosqlite.connect(s.path) as d:
            assert (await (await d.execute("SELECT COUNT(*) FROM ledger")).fetchone())[
                0
            ] == 0

    asyncio.run(run())


def test_bootstrap_off_never_reads_keys_policy_or_clients():
    class Forbidden(dict):
        def get(self, key, default=None):
            if key != "DEX_LIVE_ENABLED":
                raise AssertionError(key)
            return "false"

    assert (
        asyncio.run(
            build(
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                dict(live_enabled=True),
                env=Forbidden(),
            )
        )
        is None
    )
    assert (
        asyncio.run(
            build(
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                dict(live_enabled=False),
                env={"DEX_LIVE_ENABLED": "true"},
            )
        )
        is None
    )
    with pytest.raises(ValueError, match="FLAG_INVALID"):
        enabled({"DEX_LIVE_ENABLED": "maybe"})


def test_configured_bootstrap_composes_without_rpc_or_send(tmp_path, monkeypatch):
    async def run():
        import json
        import app.dex_live_bootstrap as bootstrap
        from app.dex_wallet import Journal
        from eth_account import Account
        from test_dex_live_bridge import ASSET, QUOTE

        account = Account.create()
        policy = tmp_path / "policy.json"
        policy.write_text(
            json.dumps(
                dict(
                    version=1,
                    wallet=account.address,
                    cex_venue="bybit",
                    acceptance_path=str(tmp_path / "missing-certificate.json"),
                    targets=[ASSET],
                    max_sell_raw={QUOTE: "5000000", ASSET: "100"},
                    max_gas_raw="1000000000000000",
                )
            )
        )
        calls = []

        class Provider:
            def __init__(self, *args):
                calls.append("constructed")

            async def close(self):
                calls.append("closed")

            async def firm(self, *args, **kwargs):
                raise AssertionError("network")

        monkeypatch.setattr(bootstrap, "Provider", Provider)
        co = await build(
            tmp_path / "db.sqlite",
            Journal(tmp_path / "db.sqlite"),
            NS(url="https://primary.invalid"),
            NS(url="https://secondary.invalid"),
            {"bybit": NS()},
            {"bybit": NS()},
            NS(),
            dict(
                live_enabled=True,
                entry_authority=lambda v: False,
                exit_authority=lambda v: False,
                acceptance_path="missing.json",
            ),
            env=dict(
                DEX_LIVE_ENABLED="true",
                DEX_WALLET_POLICY_PATH=str(policy),
                DEX_WALLET_PRIVATE_KEY=account.key.hex(),
                DEX_LIVE_ROUTES_JSON=json.dumps(
                    [
                        dict(
                            asset_token=ASSET,
                            asset_amount_raw="100",
                            direction="forward",
                        )
                    ]
                ),
            ),
        )
        assert isinstance(co, Coordinator) and calls == ["constructed"]
        assert await co.source.cycle() == []
        assert not await co.session.store.active()
        await co.provider.close()
        assert calls == ["constructed", "closed"]

    asyncio.run(run())


def test_concurrent_receipt_observers_accept_identical_final_proof(tmp_path):
    async def run():
        from tests.test_dex_wallet_backend import setup as wallet_setup

        e, _, journal, a, b, sender, reader = await wallet_setup(tmp_path)
        await sender.send("t", "i", e)
        original = journal.transition
        barrier = asyncio.Event()
        arrived = 0

        async def transition(*args, **kwargs):
            nonlocal arrived
            arrived += 1
            if arrived == 2:
                barrier.set()
            await asyncio.wait_for(barrier.wait(), 2)
            return await original(*args, **kwargs)

        journal.transition = transition
        results = await asyncio.gather(reader.reconcile("i"), reader.reconcile("i"))
        assert [r["status"] for r in results] == ["FINALIZED_SUCCESS"] * 2
        assert sum(m == "eth_sendRawTransaction" for m, _ in a.calls) == 1

    asyncio.run(run())


def test_source_cycle_only_exposes_sanitized_single_use_candidate(tmp_path):
    async def run():
        import json
        from tests.test_dex_live_admission import setup as admission_setup
        from app.dex_firm_simulation import Envelope

        admission, p, _ = await admission_setup(tmp_path, "none")

        async def accept(*args):
            return True

        accept.latest = dict(ceiling="0.1", mode="MODEL_NOT_FILL")

        class Provider:
            async def firm(self, *args, **kwargs):
                assert kwargs["execution_envelope"] and kwargs["exact_out"]
                return Envelope(
                    dict(quote_fingerprint="offline"),
                    dict(data="PRIVATE_CALLDATA_TEST"),
                    p.wallet,
                )

        source = Source(
            Provider(),
            lambda: json.loads((tmp_path / "registry.json").read_text()),
            NS(wallet=p.wallet, cex_venue=p.venue),
            admission.backend.clients,
            [dict(asset_token=p.asset, asset_amount_raw="100", direction="forward")],
            accept,
            clock=lambda: NOW,
            entry_gate=lambda v: True,
        )
        rows = await source.cycle()
        assert len(rows) == 1 and rows[0]["candidate_id"], rows
        assert "PRIVATE_CALLDATA_TEST" not in json.dumps(rows)
        key = rows[0]["candidate_id"]
        assert source.take(key)[1].transaction["data"] == "PRIVATE_CALLDATA_TEST"
        await source.cycle()
        with pytest.raises(ValueError):
            source.take(key)

    asyncio.run(run())


@pytest.mark.parametrize(
    "phase,severity", [("PENDING", "WARNING"), ("UNKNOWN", "HIGH")]
)
def test_shared_monitor_distinguishes_expected_settlement_and_unknown(
    tmp_path, phase, severity
):
    async def run():
        import aiosqlite
        from app.live_monitor import Monitor
        from app.live_spot_spot_dispatch import CashObserver
        from app.live_supervisor import LiveSupervisor
        from app.persistent_stop import Stop
        from app.runtime_store import RuntimeStore
        from app.db import Diary

        s, p, w, c = await setup(tmp_path)
        await s.enter("t", p, envelope(p))
        async with aiosqlite.connect(s.path) as d:
            await d.execute("UPDATE wallet_tx_intents SET phase=?", (phase,))
            await d.commit()

        async def pending(iid):
            return dict(status="HOLD", reason="WALLET_RECEIPT_PENDING")

        w.reconcile = pending
        marker = Marker(s, NS(), NS())

        async def inventory():
            return []

        async def source():
            return {
                p.venue: dict(
                    health=NS(ok=True),
                    snapshot_started_at=NOW,
                    fetched_at=NOW,
                    positions=[],
                    orders=[],
                )
            }

        stop = Stop(tmp_path / "stop.json")
        assert stop.resume(NS(safe=True))
        m = Monitor(
            s.store,
            RuntimeStore(str(tmp_path / "runtime.json")),
            Diary(s.path),
            source,
            {},
            LiveSupervisor(),
            stop,
            cash_observer=CashObserver(NS(inventory=inventory), None, marker),
            clock=lambda: NOW,
        )
        await m.init()
        report = await m.cycle()
        assert not report["private_verified"] and not report["reconciled"]
        matching = [
            i
            for i in report["incidents"]
            if i["code"] in ("DEX_SETTLEMENT_PENDING", "CASH_PRIVATE_UNVERIFIED")
        ]
        assert len(matching) == 1 and matching[0]["severity"] == severity, report
        assert stop.stopped is (severity == "HIGH")
        assert len(w.calls) == 1 and not c.calls

    asyncio.run(run())
