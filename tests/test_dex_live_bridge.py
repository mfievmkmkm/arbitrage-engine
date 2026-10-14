"""Offline lifecycle tests. No real keys, RPC, credentials or exchange sends."""

import asyncio
import json
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace as NS
import aiosqlite
import pytest
from app.dex_live_bridge import Session, Plan, wallet_flow
from app.dex_cex_backend import rebuild, snapshot
from app.dex_wallet import Journal
from app.dex_firm_simulation import Envelope

NOW = 1800000000
ASSET, QUOTE, WALLET = ("0x" + x * 40 for x in ("1", "2", "3"))


def envelope(p, closing=False, raw=100):
    forward = p.direction == "forward"
    sell, buy = (QUOTE, ASSET) if forward else (ASSET, QUOTE)
    if closing:
        sell, buy = buy, sell
    mode = "exact_in" if forward == closing else "exact_out"
    return Envelope(
        dict(
            sell_token=sell,
            buy_token=buy,
            quote_mode=mode,
            requested_amount_raw=str(abs(raw)),
            quote_fingerprint="q-" + str(closing),
        ),
        {},
        WALLET,
    )


class Wallet:
    def __init__(self, path, clock, p):
        self.journal, self.clock, self.p = Journal(path, clock), clock, p
        self.policy = NS(check=lambda e, n: None, cex_venue=p.venue)
        self.calls = []
        self.unknown = False
        self.revert = False
        self.balance = {ASSET: 1000, QUOTE: 10000}
        self.native = 100000

    async def send(self, tid, iid, e, closing=False):
        before = {k: str(v) for k, v in self.balance.items()}
        q = e.proof
        self.calls.append(iid)
        a = int(q["requested_amount_raw"])
        if self.revert:
            delta = {ASSET: 0, QUOTE: 0}
        elif not closing:
            delta = {
                ASSET: a if self.p.direction == "forward" else -a,
                QUOTE: -400 if self.p.direction == "forward" else 420,
            }
        else:
            delta = {
                ASSET: -a if self.p.direction == "forward" else a,
                QUOTE: 410 if self.p.direction == "forward" else -390,
            }
        for k, v in delta.items():
            self.balance[k] += v
        nonce = len(self.calls)
        self.native -= 100
        receipt = dict(
            verified=True,
            finalized=True,
            chain_id=1,
            tx_hash="0x" + str(nonce).rjust(64, "0"),
            status=0 if self.revert else 1,
            gas_paid_raw="100",
            native_before_raw=str(self.native + 100),
            native_after_raw=str(self.native),
            before=before,
            after={k: str(v) for k, v in self.balance.items()},
            token_deltas={k: str(v) for k, v in delta.items()},
        )
        payload = dict(proof=q, receipt=receipt, cex_venue=self.p.venue)
        expected = "DEX_EXIT_SUBMITTING" if closing else "PLANNED"
        async with aiosqlite.connect(self.journal.path) as d:
            await d.execute("BEGIN IMMEDIATE")
            cur = await d.execute(
                "UPDATE live_trades SET phase='DEX_WALLET_PENDING' WHERE trade_id=? AND phase=?",
                (tid, expected),
            )
            assert cur.rowcount == 1
            await d.execute(
                "INSERT INTO wallet_tx_intents VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    iid,
                    tid,
                    1,
                    WALLET,
                    nonce,
                    "FINALIZED_REVERT" if self.revert else "FINALIZED_SUCCESS",
                    receipt["tx_hash"],
                    NOW,
                    NOW,
                    json.dumps(payload),
                ),
            )
            await d.commit()
        if self.unknown:
            raise TimeoutError()

    async def reconcile(self, iid):
        if self.unknown:
            return dict(status="HOLD", reason="PENDING")
        row = await self.journal.get(iid)
        return dict(status=row["phase"])

    async def inventory(self, wallet, tokens):
        return dict(
            verified=True,
            wallet=WALLET,
            chain_id=1,
            nonce=len(self.calls) + 1,
            native_raw=str(self.native),
            balances={t: str(v) for t, v in self.balance.items()},
            ts=NOW,
        )


class CEX:
    def __init__(self, p, path):
        self.p, self.calls, self.stages = p, [], []
        self.path = path
        self.base, self.realized, self.fees = Decimal(0), Decimal(0), Decimal(0)
        self.fraction = Decimal(1)
        self.unknown = False

    async def prepare(self, p, raw, closing):
        return NS(raw=raw, closing=closing)

    async def send(self, tid, stage, p, request, closing):
        self.calls.append(stage)
        self.stages.append(stage)
        iid = tid + ":cash:" + stage
        price = (
            ("4.2" if not closing else "4")
            if p.direction == "forward"
            else ("4" if not closing else "4.2")
        )
        payload = dict(
            intent_id=iid,
            trade_id=tid,
            venue=p.venue,
            symbol=p.symbol,
            side="buy" if request.raw > 0 else "sell",
            qty=str(abs(Decimal(request.raw)) / 100),
            reduce_only=closing,
            state="UNKNOWN" if self.unknown else "FILLED",
            filled=str(abs(Decimal(request.raw)) / 100 * self.fraction),
            fee="0.01" if self.fraction else "0",
            avg_price=price,
            order_id=stage,
        )
        async with aiosqlite.connect(self.path) as d:
            await d.execute(
                "INSERT INTO order_intents VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    iid,
                    tid,
                    p.venue,
                    p.symbol,
                    payload["side"],
                    float(payload["qty"]),
                    int(closing),
                    payload["state"],
                    NOW,
                    json.dumps(payload),
                ),
            )
            await d.commit()
        if self.unknown:
            raise TimeoutError()
        self.base += Decimal(request.raw) / 100 * self.fraction
        self.fees += Decimal("0.01")
        if closing:
            self.realized += Decimal("0.2")

    async def reconcile(self, tid, p):
        if self.unknown and self.calls:
            raise ValueError("UNKNOWN_ORDER")
        async with aiosqlite.connect(self.path) as d:
            cur = await d.execute(
                "SELECT rowid,intent_id,payload FROM order_intents WHERE trade_id=?",
                (tid,),
            )
            intents = {
                iid: dict(json.loads(payload), _journal_sequence=seq)
                for seq, iid, payload in await cur.fetchall()
            }
        return dict(
            rebuild(intents, tid, p),
            verified=True,
            ts=NOW,
            journal_snapshot=snapshot(intents),
        )


async def setup(tmp_path, direction="forward"):
    path = tmp_path / "db.sqlite"
    p = Plan(
        "bybit",
        "TEST/USDT:USDT",
        WALLET,
        ASSET,
        QUOTE,
        2,
        2,
        "1",
        direction,
        NOW,
        "5",
        "0.01",
    )
    wallet, cex = Wallet(path, lambda: NOW, p), CEX(p, path)

    async def admission(*args):
        return True

    async def reverse(plan, raw):
        return envelope(plan, True, raw)

    async def costs(plan, obs, closed):
        return dict(
            verified=True,
            trade_id="t",
            venue=plan.venue,
            symbol=plan.symbol,
            gas_raw=str(obs["wallet"]["gas_raw"]),
            hashes=obs["wallet"]["hashes"],
            funding_covered_until=closed,
            gas_valuation_evidence="observed-gas-book",
            quote_usdt_identity_evidence="verified-token-registry",
            gas_usdt="0.02",
            funding="-0.03",
        )

    session = Session(
        path,
        wallet,
        wallet,
        cex,
        admission,
        reverse,
        costs,
        lambda v: True,
        lambda v: True,
        hedge_admission=admission,
        clock=lambda: NOW,
    )
    await session.init()
    await wallet.journal.init()
    from app.db import Diary

    await Diary(path).init()
    return session, p, wallet, cex


@pytest.mark.parametrize("direction,net", [("forward", "0.22"), ("reverse", "0.42")])
def test_both_directions_exact_final_accounting(tmp_path, direction, net):
    async def run():
        s, p, w, c = await setup(tmp_path, direction)
        assert (await s.enter("t", p, envelope(p)))["status"] == "VERIFIED"
        assert (await s.hedge("t"))["status"] == "OPEN"
        assert (await s.close("t"))["status"] == "ACCOUNTING_PENDING"
        assert (await s.finalize("t"))["net"] == net
        assert (await s.finalize("t"))["status"] == "ALREADY_CLOSED"
        assert len(w.calls) == 2 and c.calls == ["hedge", "exit"]
        assert not await s.store.active()
        async with aiosqlite.connect(s.path) as d:
            assert (await (await d.execute("SELECT COUNT(*) FROM ledger")).fetchone())[
                0
            ] == 1

    asyncio.run(run())


def test_entry_unknown_never_resends_or_hedges(tmp_path):
    async def run():
        s, p, w, c = await setup(tmp_path)
        w.unknown = True
        assert (await s.enter("t", p, envelope(p)))["status"] == "HOLD"
        assert (await s.enter("t", p, envelope(p)))["status"] == "RECONCILE_REQUIRED"
        assert (await s.hedge("t"))["status"] == "HOLD"
        assert (await s.close("t", recovery=True))["status"] == "HOLD"
        assert len(w.calls) == 1 and not c.calls and await s.store.active()

    asyncio.run(run())


def test_revert_charges_gas_but_does_not_open_cex(tmp_path):
    async def run():
        s, p, w, c = await setup(tmp_path)
        w.revert = True
        await s.enter("t", p, envelope(p))
        assert (await s.hedge("t"))["status"] == "ACCOUNTING_PENDING"
        result = await s.finalize("t")
        assert result["status"] == "CLOSED" and Decimal(result["net"]) == Decimal(
            "-0.06"
        )
        assert not c.calls

    asyncio.run(run())


def test_partial_hedge_unwinds_actual_cex_then_wallet(tmp_path):
    async def run():
        s, p, w, c = await setup(tmp_path)
        await s.enter("t", p, envelope(p))
        c.fraction = Decimal("0.5")
        assert (await s.hedge("t"))["status"] == "RECOVERY_REQUIRED"
        assert (await s.close("t"))["status"] == "RECONCILE_REQUIRED"
        c.fraction = Decimal(1)
        assert (await s.close("t", recovery=True))["status"] == "ACCOUNTING_PENDING"
        assert c.base == 0 and len(w.calls) == 2

    asyncio.run(run())


def test_partial_close_does_not_reverse_wallet_until_private_flat(tmp_path):
    async def run():
        s, p, w, c = await setup(tmp_path)
        await s.enter("t", p, envelope(p))
        await s.hedge("t")
        c.fraction = Decimal("0.5")
        assert (await s.close("t"))["reason"] == "DEX_CEX_CLOSE_PARTIAL"
        assert len(w.calls) == 1
        c.fraction = Decimal(1)
        assert (await s.close("t", recovery=True))["status"] == "ACCOUNTING_PENDING"
        assert len(w.calls) == 2

    asyncio.run(run())


def test_cex_unknown_preserves_capacity_and_wallet(tmp_path):
    async def run():
        s, p, w, c = await setup(tmp_path)
        await s.enter("t", p, envelope(p))
        c.unknown = True
        assert (await s.hedge("t"))["status"] == "HOLD"
        assert (await s.close("t", recovery=True))["status"] == "HOLD"
        assert len(w.calls) == 1 and c.calls == ["hedge"]
        assert await s.store.active()

    asyncio.run(run())


def test_concurrent_hedge_claim_is_single_send(tmp_path):
    async def run():
        s, p, w, c = await setup(tmp_path)
        await s.enter("t", p, envelope(p))
        await asyncio.gather(s.hedge("t"), s.hedge("t"))
        assert c.calls == ["hedge"]

    asyncio.run(run())


def test_claim_without_intent_is_not_proof_of_zero_execution(tmp_path):
    async def run():
        s, p, w, c = await setup(tmp_path)
        await s.enter("t", p, envelope(p))
        await s._change(
            "t",
            ("DEX_WALLET_PENDING",),
            "DEX_HEDGE_SUBMITTING",
            stage="hedge",
            kind="CEX",
            expected_cex=True,
        )
        assert (await s.observe("t"))[
            "reason"
        ] == "DEX_CEX_CLAIM_WITHOUT_TERMINAL_INTENT"
        assert (await s.close("t", recovery=True))["status"] == "HOLD"
        assert len(w.calls) == 1

    asyncio.run(run())


def test_missing_post_receipt_edge_never_opens_cex(tmp_path):
    async def run():
        s, p, w, c = await setup(tmp_path)
        s.hedge_admission = None
        await s.enter("t", p, envelope(p))
        assert (await s.hedge("t"))["reason"] == "DEX_POST_RECEIPT_NET_UNVERIFIED"
        assert not c.calls

    asyncio.run(run())


def test_missing_mature_costs_keeps_shared_owner(tmp_path):
    async def run():
        s, p, w, c = await setup(tmp_path)
        await s.enter("t", p, envelope(p))
        await s.hedge("t")
        await s.close("t")

        async def bad(*a):
            return dict(verified=True)

        s.costs = bad
        assert (await s.finalize("t"))["status"] == "ACCOUNTING_PENDING"
        assert await s.store.active()

    asyncio.run(run())


@pytest.mark.parametrize("direction", ["forward", "reverse"])
def test_cex_native_terminal_rebuild(direction):
    p = Plan(
        "bybit",
        "TEST/USDT:USDT",
        WALLET,
        ASSET,
        QUOTE,
        2,
        2,
        "0.1",
        direction,
        NOW,
        "5",
        "0",
    )
    intents = {}
    for n, stage in enumerate(("hedge", "exit"), 1):
        iid = "t:cash:" + stage
        side = "sell" if (stage == "hedge") == (direction == "forward") else "buy"
        intents[iid] = dict(
            intent_id=iid,
            trade_id="t",
            venue=p.venue,
            symbol=p.symbol,
            state="FILLED",
            reduce_only=stage != "hedge",
            side=side,
            filled=10,
            qty=10,
            fee="0.01",
            avg_price=4.2 if n == 1 else 4,
            order_id=str(n),
            _journal_sequence=n,
        )
    result = rebuild(intents, "t", p)
    assert Decimal(result["base"]) == 0 and Decimal(result["fees"]) == Decimal("0.02")
    assert Decimal(result["realized"]) == Decimal(
        "0.2" if direction == "forward" else "-0.2"
    )
    intents["t:cash:exit"]["filled"] = 11
    intents["t:cash:exit"]["qty"] = 11
    with pytest.raises(ValueError, match="OVERFILL"):
        rebuild(intents, "t", p)


@pytest.mark.parametrize("kind", ["asset", "native", "nonce"])
def test_external_wallet_movement_is_hold(tmp_path, kind):
    async def run():
        s, p, w, c = await setup(tmp_path)
        await s.enter("t", p, envelope(p))
        if kind == "asset":
            w.balance[ASSET] += 1
        elif kind == "native":
            w.native += 1
        else:
            w.calls.append("unmanaged")
        assert (await s.hedge("t"))["reason"] == "DEX_WALLET_CURRENT_STATE_CHANGED"
        assert not c.calls and await s.store.active()

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["wallet", "cex"])
def test_proof_mutation_during_final_cost_fetch_does_not_commit(tmp_path, kind):
    async def run():
        s, p, w, c = await setup(tmp_path)
        await s.enter("t", p, envelope(p))
        await s.hedge("t")
        await s.close("t")
        original = s.costs

        async def mutate(*args):
            result = await original(*args)
            async with aiosqlite.connect(s.path) as d:
                table = "wallet_tx_intents" if kind == "wallet" else "order_intents"
                cur = await d.execute(
                    "SELECT intent_id,payload FROM " + table + " LIMIT 1"
                )
                iid, payload = await cur.fetchone()
                data = json.loads(payload)
                data["concurrent_change"] = True
                await d.execute(
                    "UPDATE " + table + " SET payload=? WHERE intent_id=?",
                    (json.dumps(data), iid),
                )
                await d.commit()
            return result

        s.costs = mutate
        assert (await s.finalize("t"))["status"] == "ACCOUNTING_PENDING"
        async with aiosqlite.connect(s.path) as d:
            assert (await (await d.execute("SELECT COUNT(*) FROM ledger")).fetchone())[
                0
            ] == 0
        assert await s.store.active()

    asyncio.run(run())


def test_concurrent_finalizers_credit_exactly_once(tmp_path):
    async def run():
        s, p, w, c = await setup(tmp_path)
        await s.enter("t", p, envelope(p))
        await s.hedge("t")
        await s.close("t")
        results = await asyncio.gather(s.finalize("t"), s.finalize("t"))
        assert sum(r["status"] == "CLOSED" for r in results) == 1
        async with aiosqlite.connect(s.path) as d:
            assert (
                await (await d.execute("SELECT COUNT(*) FROM live_results")).fetchone()
            )[0] == 1

    asyncio.run(run())


def test_restart_only_observes_then_explicit_recovery(tmp_path):
    async def run():
        s, p, w, c = await setup(tmp_path)
        await s.enter("t", p, envelope(p))
        # New process/session object shares DB and read adapters, no startup send.
        restored = Session(
            s.path,
            w,
            w,
            c,
            s.admission,
            s.reverse_quote,
            s.costs,
            s.entry_authority,
            s.exit_authority,
            hedge_admission=s.hedge_admission,
            clock=s.clock,
        )
        for _ in range(3):
            assert (await restored.observe("t"))["status"] == "VERIFIED"
        assert len(w.calls) == 1 and not c.calls
        assert (await restored.close("t", recovery=True))[
            "status"
        ] == "ACCOUNTING_PENDING"
        assert len(w.calls) == 2 and not c.calls

    asyncio.run(run())


def test_bounded_recovery_retains_inventory_after_limit(tmp_path):
    async def run():
        s, p, w, c = await setup(tmp_path)
        await s.enter("t", p, envelope(p))
        await s.hedge("t")
        c.fraction = Decimal(0)
        await s.close("t")
        for _ in range(3):
            assert (await s.close("t", recovery=True))["status"] == "RECOVERY_REQUIRED"
        assert (await s.close("t", recovery=True))[
            "reason"
        ] == "DEX_RECOVERY_ROUND_LIMIT"
        assert len(w.calls) == 1 and await s.store.active()

    asyncio.run(run())


def test_actual_safe_executor_and_native_private_backend(tmp_path):
    async def run():
        from app.dex_cex_backend import Backend
        from app.db import Diary
        from app.live_trade_store import Store
        from tests.test_spot_future_live_session import Clock, exchange
        from tests.test_spot_live_units import FS

        clock, state = Clock(), {"spot": 0, "future": 0}
        client = exchange(clock, False, state)
        diary, store = Diary(str(tmp_path / "actual.sqlite")), Store(
            tmp_path / "actual.sqlite"
        )
        await diary.init()
        await store.init()
        p = Plan(
            "binance",
            FS,
            WALLET,
            ASSET,
            QUOTE,
            2,
            2,
            "0.001",
            "forward",
            clock(),
            "5",
            "0",
        )
        b = Backend(
            store, diary, {"binance": client}, lambda v: True, lambda v: True, clock
        )
        r = await b.prepare(p, -4, closing=False)
        await b.send("t", "hedge", p, r, closing=False)
        assert Decimal((await b.reconcile("t", p))["base"]) == Decimal("-0.04")
        r = await b.prepare(p, 4, closing=True)
        await b.send("t", "exit", p, r, closing=True)
        result = await b.reconcile("t", p)
        assert Decimal(result["base"]) == 0 and len(result["journal_snapshot"]) == 2
        assert len(client.sent) == 2 and client.sent[-1][-1]["reduceOnly"] is True

    asyncio.run(run())


def test_runtime_observer_never_advances_or_sends(tmp_path):
    async def run():
        from app.dex_live_observer import Observer

        s, p, w, c = await setup(tmp_path)
        await s.enter("t", p, envelope(p))
        observer = Observer(s)
        for _ in range(2):
            rows = await observer.cycle()
            assert rows[0]["status"] == "VERIFIED" and rows[0]["live_allowed"] is False
        assert len(w.calls) == 1 and not c.calls
        assert (await s.store.get("t"))["phase"] == "DEX_WALLET_PENDING"

    asyncio.run(run())


def test_shared_monitor_recognizes_dex_cex_owner_without_false_flat(tmp_path):
    async def run():
        from app.dex_live_observer import Observer
        from app.live_monitor import Monitor
        from app.live_spot_spot_dispatch import CashObserver
        from app.live_supervisor import LiveSupervisor
        from app.persistent_stop import Stop
        from app.runtime_store import RuntimeStore
        from app.db import Diary

        s, p, w, c = await setup(tmp_path)
        await s.enter("t", p, envelope(p))
        await s.hedge("t")

        async def inventory():
            return []

        cash = CashObserver(NS(inventory=inventory), None, Observer(s))

        async def source():
            return {
                p.venue: dict(
                    health=NS(ok=True),
                    snapshot_started_at=NOW,
                    fetched_at=NOW,
                    positions=[NS(symbol=p.symbol, side="short", qty=1)],
                    orders=[],
                )
            }

        m = Monitor(
            s.store,
            RuntimeStore(str(tmp_path / "runtime.json")),
            Diary(s.path),
            source,
            {},
            LiveSupervisor(),
            Stop(tmp_path / "stop.json"),
            cash_observer=cash,
            clock=lambda: NOW,
        )
        await m.init()
        result = await m.cycle()
        assert result["private_verified"] is True and result["reconciled"] is True
        assert not any(i["code"] == "UNMANAGED_POSITION" for i in result["incidents"])
        assert (await s.store.get("t"))["phase"] == "DEX_OPEN"
        assert result["trades"][0]["strategy"] == "cex_dex"
        assert len(w.calls) == 1 and c.calls == ["hedge"]

    asyncio.run(run())
