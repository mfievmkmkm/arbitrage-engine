"""Offline native journals only: no exchange, RPC, signing or real orders."""

import asyncio
import json
from dataclasses import asdict
from decimal import Decimal
import aiosqlite
import pytest
from app.live_execution_costs import build, render
from app.live_cash_dex_attribution import USDT
from tests.test_spot_future_live_session import setup as sf_setup
from tests.test_remaining_live import setup as ss_setup, offer


async def cash_closed(tmp_path, strategy, base_fee=True):
    if strategy == "spot_futures":
        s, op, spot, future, _, clock, *_ = await sf_setup(tmp_path)
        if not base_fee:
            spot.zero_fee = True
        assert (await s.enter(op, "t"))["status"] == "OPEN"
        future.sell_price, future.buy_price = 99.99, 100.01
        assert (await s.close("t"))["status"] == "ACCOUNTING_PENDING"
        clock.now += 31
    else:
        s, clients, _, _, _ = await ss_setup(tmp_path, base_fee)
        assert (await s.enter(offer(), "t"))["status"] == "OPEN"
        clients["b"].buy_price = 100
        clients["b"].sell_price = 99.99
        assert (await s.close("t"))["status"] == "ACCOUNTING_PENDING"
    assert (await s.finalize("t"))["status"] == "CLOSED"
    return s


@pytest.mark.parametrize("strategy", ["spot_futures", "spot_spot"])
@pytest.mark.parametrize("base_fee", [True, False])
def test_cash_exact_reconciliation_with_explicit_inventory_and_no_double_fees(
    tmp_path, strategy, base_fee
):
    async def go():
        s = await cash_closed(tmp_path, strategy, base_fee)
        report = await build(s.store.path)
        row = report["trades"][0]
        assert row["status"] == "RECONCILED", row
        assert row["net"] == pytest.approx(row["gross"] - row["fees"] + row["funding"])
        assert row["trading_fees"] == row["fees"] and row["gas"] == 0
        assert row["base_fee_usd"] > 0 if base_fee else row["base_fee_usd"] == 0
        assert any(r["market_type"] == "spot" for r in report["orders"])
        assert sum(
            r["fees"] + r["base_fee_usd"] for r in report["orders"]
        ) == pytest.approx(row["fees"])
        if strategy == "spot_futures" and base_fee:
            assert (
                row["phase"] == "CLOSED_WITH_INVENTORY"
                and row["held_inventory_cost"] > 0
            )
            assert row["inventory_valuation_in_net"] == 0
        original = await s.store.get("t")
        assert await build(s.store.path) == report
        assert await s.store.get("t") == original
        assert (
            "Спот ↔ Фьючерсы" if strategy == "spot_futures" else "Спот ↔ Спот"
        ) in render(report)

    asyncio.run(go())


@pytest.mark.parametrize("strategy", ["spot_futures", "spot_spot"])
@pytest.mark.parametrize(
    "fault",
    [
        "fees",
        "inventory",
        "proof",
        "journal",
        "quote",
        "missing_quote",
        "unknown",
        "payload",
    ],
)
def test_cash_conflicts_do_not_become_certified_net(tmp_path, strategy, fault):
    async def go():
        s = await cash_closed(tmp_path, strategy)
        async with aiosqlite.connect(s.store.path) as db:
            if fault == "fees":
                await db.execute("UPDATE live_results SET fees=fees+1")
            if fault == "inventory":
                await db.execute(
                    "UPDATE live_cash_inventory SET qty=qty+1"
                    if strategy == "spot_futures"
                    else "UPDATE live_spot_allocations SET delta=delta+1"
                )
            if fault in ("proof", "payload"):
                row = await s.store.get("t")
                p = json.loads(row["payload"])
                if fault == "payload":
                    p["result"]["net"] += 1
                elif strategy == "spot_futures":
                    p["cash_close_proof"]["future_flat"] = False
                else:
                    p["close_proof"]["verified"] = False
                await db.execute("UPDATE live_trades SET payload=?", (json.dumps(p),))
            iid = next(iter(await s.diary.order_intents("t")))
            if fault in ("journal", "unknown"):
                p = (await s.diary.order_intents("t"))[iid]
                if fault == "journal":
                    p["base_fee"] += 1
                else:
                    p["state"] = "UNKNOWN"
                await db.execute(
                    "UPDATE order_intents SET payload=?,state=? WHERE intent_id=?",
                    (json.dumps(p), p["state"], iid),
                )
            if fault == "quote":
                p = await s.diary.order_request_evidence(iid)
                p["market_evidence"]["book_ts"] -= 3
                await db.execute(
                    "UPDATE order_request_evidence SET payload=? WHERE intent_id=?",
                    (json.dumps(p), iid),
                )
            if fault == "missing_quote":
                await db.execute(
                    "DELETE FROM order_request_evidence WHERE intent_id=?", (iid,)
                )
            await db.commit()
        row = (await build(s.store.path))["trades"][0]
        assert row["status"] == "PARTIAL" and row["reasons"], row
        assert "net" not in row

    asyncio.run(go())


@pytest.mark.parametrize("strategy", ["spot_futures", "spot_spot"])
def test_actual_second_leg_fee_breach_closes_instead_of_clamping_profit(
    tmp_path, strategy
):
    async def go():
        if strategy == "spot_futures":
            s, op, _, client, _, _, *_ = await sf_setup(tmp_path)
            state = None
        else:
            s, clients, state, _, _ = await ss_setup(tmp_path, False)
            client = clients["b"]
            op = offer()
        original = client.create_order

        async def create(symbol, kind, side, qty, price, params):
            row = await original(symbol, kind, side, qty, price, params)
            if side == "sell" and len(client.sent) == 1:
                row["fee"]["cost"] += 0.5
                if state is not None:
                    state["b"]["USDT"] -= 0.5
                client.orders[row["id"]] = row
            return row

        client.create_order = create
        outcome = await s.enter(op, "t")
        assert outcome["status"] == "ACCOUNTING_PENDING", outcome
        meta = json.loads((await s.store.get("t"))["payload"])
        assert meta["cash_close_reason"] == "ACTUAL_ENTRY_NET_BELOW_THRESHOLD"
        assert meta["cash_actual_entry_edge"] < 0.05
        assert len(client.sent) == 2
        assert (await build(s.store.path))["trades"][0]["status"] == "PARTIAL"

    asyncio.run(go())


def test_derivative_snapshot_ignores_storage_wrappers_not_economic_changes():
    from app.dex_cex_backend import snapshot

    p = dict(
        intent_id="x",
        trade_id="t",
        venue="bybit",
        symbol="TEST",
        state="FILLED",
        fee=0.01,
        _journal_sequence=1,
    )
    native = {"x": p}
    wrapped = {"x": dict(p, payload=json.dumps(p), updated_at=123)}
    assert snapshot(native) == snapshot(wrapped)
    wrapped["x"]["fee"] += 0.01
    assert snapshot(native) != snapshot(wrapped)


def test_dex_finalizer_accepts_actual_diary_wrapper_snapshot(tmp_path):
    async def go():
        from tests.test_dex_live_bridge import setup, envelope
        from app.db import Diary
        from app.dex_cex_backend import snapshot

        s, p, _, c = await setup(tmp_path)
        original = c.reconcile

        async def reconcile(tid, plan):
            r = await original(tid, plan)
            r["journal_snapshot"] = snapshot(await Diary(s.path).order_intents(tid))
            return r

        c.reconcile = reconcile
        await s.enter("t", p, envelope(p))
        assert (await s.hedge("t"))["status"] == "OPEN"
        assert (await s.close("t"))["status"] == "ACCOUNTING_PENDING"
        assert (await s.finalize("t"))["status"] == "CLOSED"

    asyncio.run(go())


@pytest.mark.parametrize("column", ["qty", "symbol", "state"])
def test_dex_finalizer_rejects_sql_payload_conflict_after_private_proof(
    tmp_path, column
):
    async def go():
        from tests.test_dex_live_bridge import setup, envelope

        s, p, _, _ = await setup(tmp_path)
        await s.enter("t", p, envelope(p))
        await s.hedge("t")
        await s.close("t")
        original = s.costs

        async def costs(*args):
            r = await original(*args)
            async with aiosqlite.connect(s.path) as db:
                await db.execute(
                    "UPDATE order_intents SET " + column + "=?",
                    (9 if column == "qty" else "UNKNOWN",),
                )
                await db.commit()
            return r

        s.costs = costs
        r = await s.finalize("t")
        assert r["status"] == "ACCOUNTING_PENDING" and "COLUMNS_CONFLICT" in r["reason"]
        async with aiosqlite.connect(s.path) as db:
            assert (
                await (await db.execute("SELECT COUNT(*) FROM live_results")).fetchone()
            )[0] == 0

    asyncio.run(go())


async def dex_closed(tmp_path, direction="forward"):
    from app.db import Diary
    from app.live_trade_store import Store
    from app.live_monitor_store import Store as Results
    from app.dex_wallet import Journal
    from app.dex_live_bridge import Plan, wallet_flow
    from app.dex_cex_backend import rebuild, snapshot
    from app.live_order_intent import OrderIntent
    from app.exchange_executor import SubmitRequest, SubmitResult
    from app.public_books import normalize
    from tests.test_dex_live_bridge import NOW, ASSET, WALLET

    path = tmp_path / "dex.db"
    diary = Diary(path)
    await diary.init()
    store = Store(path)
    await store.init()
    await Results(path).init()
    await Journal(path, lambda: NOW).init()
    p = Plan(
        "bybit",
        "TEST/USDT:USDT",
        WALLET,
        ASSET,
        USDT,
        2,
        6,
        "1",
        direction,
        NOW - 100,
        "5",
        ".01",
    )
    receipts = []
    balances = {ASSET: 1000, USDT: 20000000}
    native = 10**18
    closed = NOW - 31
    for i in (1, 2):
        asset_delta = (100 if direction == "forward" else -100) * (1 if i == 1 else -1)
        quote_delta = (
            (-4000000 if direction == "forward" else 4200000)
            if i == 1
            else (4100000 if direction == "forward" else -3900000)
        )
        before = {k: str(v) for k, v in balances.items()}
        balances[ASSET] += asset_delta
        balances[USDT] += quote_delta
        after = {k: str(v) for k, v in balances.items()}
        txhash = "0x" + str(i).rjust(64, "0")
        receipt = dict(
            verified=True,
            finalized=True,
            chain_id=1,
            tx_hash=txhash,
            status=1,
            before=before,
            after=after,
            token_deltas={ASSET: str(asset_delta), USDT: str(quote_delta)},
            gas_paid_raw=str(10**14),
            native_before_raw=str(native),
            native_after_raw=str(native - 10**14),
        )
        native -= 10**14
        payload = dict(
            proof=dict(
                sell_token=USDT if asset_delta > 0 else ASSET,
                buy_token=ASSET if asset_delta > 0 else USDT,
            ),
            receipt=receipt,
            cex_venue=p.venue,
        )
        r = dict(
            intent_id="t:wallet:" + str(i),
            trade_id="t",
            chain_id=1,
            wallet=WALLET,
            nonce=i,
            phase="FINALIZED_SUCCESS",
            tx_hash=txhash,
            created_at=NOW - 100 if i == 1 else closed,
            updated_at=closed,
            payload=json.dumps(payload),
        )
        receipts.append(r)
        async with aiosqlite.connect(path) as db:
            await db.execute(
                "INSERT INTO wallet_tx_intents VALUES(?,?,?,?,?,?,?,?,?,?)",
                tuple(r.values()),
            )
            await db.commit()
    for stage in ("hedge", "exit"):
        entry = stage == "hedge"
        side = (
            ("sell" if direction == "forward" else "buy")
            if entry
            else ("buy" if direction == "forward" else "sell")
        )
        price = (
            (4.2 if direction == "forward" else 4)
            if entry
            else (4 if direction == "forward" else 4.2)
        )
        stamp = p.opened_at if entry else closed
        e = dict(
            source="PUBLIC_IOC_ENTRY_V1" if entry else "PUBLIC_REST_RECOVERY_V1",
            venue=p.venue,
            symbol=p.symbol,
            side=side,
            contracts=1,
            contract_size=1,
            base_qty=1,
            book_ts=stamp,
            started_at=stamp,
            received_at=stamp,
            bids=[[price if side == "sell" else price - 0.01, 10]],
            asks=[[price if side == "buy" else price + 0.01, 10]],
        )
        if not entry:
            e["reduce_only"] = True
        iid = "t:cash:" + stage
        req = SubmitRequest(
            p.symbol,
            side,
            1,
            "limit" if entry else "market",
            price if entry else None,
            not entry,
            entry,
            iid,
            None if entry else price,
            e,
        )
        intent = OrderIntent(iid, "t", p.venue, p.symbol, side, 1, not entry)
        assert await diary.claim_order_intent(intent, request=req)
        await diary.save_order_intent_result(
            intent, "FILLED", SubmitResult(stage, "closed", 1, price, 0.01)
        )
        async with aiosqlite.connect(path) as db:
            await db.execute(
                "UPDATE order_request_evidence SET ts=? WHERE intent_id=?", (stamp, iid)
            )
            await db.commit()
    journal = await diary.order_intents("t")
    cex = rebuild(journal, "t", p)
    wallet = wallet_flow(receipts, "t", p)
    gasbook = normalize(
        dict(
            symbol="ETH/USDT",
            timestamp=NOW * 1000,
            bids=[[1999, 10]],
            asks=[[2000, 10]],
        ),
        "ETH/USDT",
        NOW,
        NOW,
        1.5,
    )
    event = dict(
        venue=p.venue,
        event_id="income-1",
        symbol=p.symbol,
        ts=closed,
        amount=-0.03,
        source="native_private_income",
    )
    costs = dict(
        verified=True,
        trade_id="t",
        venue=p.venue,
        symbol=p.symbol,
        gas_raw=str(wallet["gas_raw"]),
        hashes=wallet["hashes"],
        gas_usdt=".4",
        funding="-.03",
        funding_covered_until=closed,
        funding_verified_at=NOW,
        funding_events=[event],
        quote_usdt_identity_evidence=dict(chain_id=1, token=USDT, decimals=6),
        gas_valuation_evidence=dict(
            method="CURRENT_EXECUTABLE_ETH_REPLACEMENT_ASK_NOT_A_FILL",
            native_asset="ETH",
            native_raw=str(wallet["gas_raw"]),
            symbol="ETH/USDT",
            venue="public-fixture",
            book=gasbook,
            average_price=2000,
            worst_price=2000,
            market_identity=dict(spot=True, active=True, base="ETH", quote="USDT"),
        ),
    )
    obs = dict(
        status="VERIFIED",
        trade_id="t",
        wallet=wallet,
        wallet_snapshot=sorted(
            [r["intent_id"], r["phase"], r["tx_hash"], r["payload"]] for r in receipts
        ),
        wallet_inventory=dict(
            verified=True,
            wallet=WALLET,
            chain_id=1,
            nonce=3,
            balances={k: str(v) for k, v in balances.items()},
            native_raw=str(native),
            ts=NOW,
        ),
        cex=dict(cex, verified=True, journal_snapshot=snapshot(journal)),
    )
    gross = Decimal(wallet["quote_raw"]) / 10**6 + Decimal(cex["realized"])
    net = (
        gross
        - Decimal(cex["fees"])
        - Decimal(".4")
        - Decimal(".03")
        - Decimal(p.safety)
    )
    stored = dict(
        observation=obs,
        costs=costs,
        plan=asdict(p),
        gross=str(gross),
        fees=cex["fees"],
        gas=".4",
        funding="-.03",
        net=str(net),
    )
    await store.phase(
        "t",
        "CLOSED_PRIVATE_VERIFIED",
        strategy="cex_dex",
        symbol=p.symbol,
        long_venue=p.venue,
        short_venue="dex",
        dex_live_plan=asdict(p),
        dex_closed_at=closed,
        dex_result=stored,
    )
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT INTO live_results VALUES(?,?,?,?,?,?,?,?)",
            (
                "t",
                NOW,
                float(gross),
                0.42,
                -0.03,
                float(net),
                "offline",
                json.dumps(stored),
            ),
        )
        await db.commit()
    return store, stored


@pytest.mark.parametrize("direction", ["forward", "reverse"])
def test_dex_receipts_native_gas_and_private_income_reconcile(tmp_path, direction):
    async def go():
        store, stored = await dex_closed(tmp_path, direction)
        r = await build(store.path)
        row = r["trades"][0]
        assert row["status"] == "RECONCILED", row
        assert row["trading_fees"] == 0.02 and row["gas"] == 0.4
        assert row["net"] == pytest.approx(row["gross"] - 0.02 - 0.4 - 0.03 - 0.01)
        assert row["gas_raw"] == str(2 * 10**14) and row["gas_asset"] == "ETH"
        assert row["adverse_slippage_usd"] is None and not row["slippage_complete"]
        assert "не подтверждён" in render(r)
        assert await build(store.path) == r

    asyncio.run(go())


@pytest.mark.parametrize(
    "fault",
    [
        "gas",
        "native",
        "funding",
        "duplicate_income",
        "maturity",
        "quote_identity",
        "wallet",
        "cex",
        "result",
        "depth",
        "gas_identity",
        "costs_scope",
        "invalid_decimal",
    ],
)
def test_dex_contradicting_receipts_or_costs_never_get_reconciled(tmp_path, fault):
    async def go():
        store, stored = await dex_closed(tmp_path)
        meta = json.loads((await store.get("t"))["payload"])
        costs = stored["costs"]
        if fault == "gas":
            costs["gas_usdt"] = ".1"
        if fault == "funding":
            costs["funding"] = ".1"
        if fault == "duplicate_income":
            costs["funding_events"] *= 2
        if fault == "maturity":
            costs["funding_verified_at"] -= 2
        if fault == "quote_identity":
            costs["quote_usdt_identity_evidence"]["decimals"] = 18
        if fault == "wallet":
            stored["observation"]["wallet_inventory"]["native_raw"] = "0"
        if fault == "cex":
            stored["observation"]["cex"]["fees"] = "0"
        if fault == "result":
            stored["net"] = "0"
        if fault == "depth":
            costs["gas_valuation_evidence"]["book"]["asks"][0][1] = 0.000001
        if fault == "gas_identity":
            costs["gas_valuation_evidence"]["market_identity"]["quote"] = "USD"
        if fault == "costs_scope":
            costs["symbol"] = "OTHER"
        if fault == "invalid_decimal":
            costs["gas_usdt"] = "invalid"
        meta["dex_result"] = stored
        async with aiosqlite.connect(store.path) as db:
            await db.execute("UPDATE live_trades SET payload=?", (json.dumps(meta),))
            await db.execute("UPDATE live_results SET payload=?", (json.dumps(stored),))
            if fault == "native":
                cur = await db.execute(
                    "SELECT intent_id,payload FROM wallet_tx_intents ORDER BY nonce LIMIT 1"
                )
                iid, raw = await cur.fetchone()
                p = json.loads(raw)
                p["receipt"]["native_after_raw"] = "0"
                await db.execute(
                    "UPDATE wallet_tx_intents SET payload=? WHERE intent_id=?",
                    (json.dumps(p), iid),
                )
            await db.commit()
        row = (await build(store.path))["trades"][0]
        assert row["status"] == "PARTIAL" and row["reasons"], row
        assert "net" not in row

    asyncio.run(go())
