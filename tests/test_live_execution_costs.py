import asyncio
import json
import aiosqlite
import pytest
from app.live_execution_costs import build, render
from app.live_monitor_store import Store as MonitorStore
from app.live_order_intent import OrderIntent
from app.exchange_executor import SubmitRequest, SubmitResult
from app.recovery_market import Reader
from tests.test_live_entry_dispatch import setup, op
from tests.test_market_fallback_dispatch import hybrid_setup


async def closed(tmp_path, hybrid=False, funding=0.001):
    c, clients, _, _ = await (hybrid_setup(tmp_path) if hybrid else setup(tmp_path))
    assert (await c.process([op()]))["opened"]
    trade = c.runtime.load()[0]
    entries = await c.diary.order_intents()
    fees = sum(r["fee"] for r in entries.values())
    gross = sum(
        r["filled"] * 0.01 * (r["avg_price"] or 0) * (1 if r["side"] == "sell" else -1)
        for r in entries.values()
    )
    for venue, side in (("a", "sell"), ("b", "buy")):
        iid = trade.trade_id + ":exit-" + venue
        req = await Reader(clients).quote(
            venue,
            SubmitRequest(op()["symbol"], side, 4, "market", None, True, False, iid),
            0.01,
        )
        intent = OrderIntent(iid, trade.trade_id, venue, req.symbol, side, 4, True)
        assert await c.diary.claim_order_intent(intent, request=req)
        fee = 4 * 0.01 * req.reference_price * 0.0005
        await c.diary.save_order_intent_result(
            intent,
            "FILLED",
            SubmitResult(venue + "exit", "closed", 4, req.reference_price, fee),
        )
        fees += fee
        gross += 0.04 * req.reference_price * (1 if side == "sell" else -1)
    store = MonitorStore(c.diary.path)
    if funding:
        await store.settlements(
            trade.trade_id,
            [dict(venue="a", event_id="f1", symbol=trade.symbol, ts=1, amount=funding)],
        )
    await store.finalize(
        trade.trade_id,
        dict(
            gross=gross,
            fees=fees,
            funding=funding,
            net=gross - fees + funding,
            reason="offline",
        ),
        dict(private_flat=True, orders_terminal=True, funding_verified=True),
    )
    return c, trade


@pytest.mark.parametrize("hybrid", [False, True])
@pytest.mark.parametrize("funding", [0, 0.01, -0.01])
def test_exact_actual_price_net_and_fee_funding_reconcile(tmp_path, hybrid, funding):
    async def go():
        c, trade = await closed(tmp_path, hybrid, funding)
        result = await build(c.diary.path)
        row = result["trades"][0]
        assert row["status"] == "RECONCILED", row
        assert row["funding"] == funding
        assert row["net"] == pytest.approx(row["gross"] - row["fees"] + funding)
        assert row["adverse_slippage_usd"] == pytest.approx(
            row["reference_gross"] - row["gross"]
        )
        assert len(result["orders"]) == (6 if hybrid else 4)
        assert result["execution_authority"] is False
        assert result["slippage_is_explanatory"] is True
        before = await c.durable.get(trade.trade_id)
        assert await build(c.diary.path) == result
        assert await c.durable.get(trade.trade_id) == before

    asyncio.run(go())


@pytest.mark.parametrize(
    "case",
    [
        "fee",
        "funding",
        "gross",
        "net",
        "missing",
        "proof",
        "scope",
        "state",
        "fill",
        "units",
        "journal",
        "close",
        "cash",
        "nan",
        "base_fee",
    ],
)
def test_missing_or_conflicting_evidence_never_gets_reconciled(tmp_path, case):
    async def go():
        c, trade = await closed(tmp_path)
        iid = next(iter(await c.diary.order_intents()))
        async with aiosqlite.connect(c.diary.path) as db:
            if case in ("fee", "funding", "gross", "net"):
                column = "fees" if case == "fee" else case
                await db.execute(
                    "UPDATE live_results SET " + column + "=" + column + "+1"
                )
            if case == "missing":
                await db.execute(
                    "DELETE FROM order_request_evidence WHERE intent_id=?", (iid,)
                )
            if case in ("proof", "scope", "units"):
                p = await c.diary.order_request_evidence(iid)
                if case == "proof":
                    p["market_evidence"]["book_ts"] -= 3
                if case == "scope":
                    p["side"] = "sell" if p["side"] == "buy" else "buy"
                if case == "units":
                    p["market_evidence"]["contract_size"] = 0.02
                await db.execute(
                    "UPDATE order_request_evidence SET payload=? WHERE intent_id=?",
                    (json.dumps(p), iid),
                )
            if case == "state":
                await db.execute(
                    "UPDATE order_intents SET state='UNKNOWN' WHERE intent_id=?", (iid,)
                )
            if case in ("fill", "journal", "nan", "base_fee"):
                p = dict((await c.diary.order_intents())[iid])
                p.pop("_journal_sequence", None)
                if case == "fill":
                    p["filled"] = 5
                if case == "journal":
                    p["symbol"] = "OTHER"
                if case == "nan":
                    p["fee"] = float("nan")
                if case == "base_fee":
                    p["base_fee"] = 0.001
                await db.execute(
                    "UPDATE order_intents SET payload=? WHERE intent_id=?",
                    (json.dumps(p), iid),
                )
            if case in ("close", "cash"):
                p = json.loads((await c.durable.get(trade.trade_id))["payload"])
                if case == "close":
                    p["close_proof"]["private_flat"] = False
                else:
                    p["strategy"] = "spot_spot"
                await db.execute("UPDATE live_trades SET payload=?", (json.dumps(p),))
            await db.commit()
        row = (await build(c.diary.path))["trades"][0]
        assert row["status"] == "PARTIAL" and row["reasons"], row
        assert "net" not in row

    asyncio.run(go())


def test_open_cycle_is_observed_not_realized_profit(tmp_path):
    async def go():
        c, *_ = await setup(tmp_path)
        assert (await c.process([op()]))["opened"]
        r = await build(c.diary.path)
        assert r["trades"][0]["status"] == "PARTIAL"
        assert "net" not in r["trades"][0]
        assert r["trades"][0]["observed_fees"] > 0

    asyncio.run(go())


def test_no_schema_and_no_silent_creation(tmp_path):
    async def go():
        path = tmp_path / "absent.db"
        with pytest.raises(aiosqlite.OperationalError):
            await build(path)
        assert not path.exists()
        async with aiosqlite.connect(path) as db:
            await db.commit()
        result = await build(path)
        assert result["status"] == "NO_DATA" and not result["trades"]

    asyncio.run(go())


def test_report_render_escapes_untrusted_trade_ids_and_explains_slippage():
    report = dict(
        trades=[dict(trade_id="<script>", status="PARTIAL", reasons=["<broken>"])]
    )
    text = render(report)
    assert "<script>" not in text and "&lt;script&gt;" in text
    assert "не вычитается" in text


def test_attribution_is_in_audit_export_and_telegram_navigation(tmp_path):
    async def go():
        from app.audit_export import build as export
        from app.tg_ui import replay_menu
        import zipfile

        c, *_ = await closed(tmp_path)
        _, archive = await export(c.diary.path, tmp_path / "export")
        with zipfile.ZipFile(archive) as z:
            assert any(
                "live_cost_attribution" in name.lower() for name in z.namelist()
            ), z.namelist()
            assert any(
                "live_order_attribution" in name.lower() for name in z.namelist()
            )
        assert any(
            b.callback_data == "live_costs"
            for row in replay_menu("live_costs").inline_keyboard
            for b in row
        )
        from app import main

        original = main.config.db_path
        try:
            object.__setattr__(main.config, "db_path", c.diary.path)
            assert "Сверено: 1 / 1" in await main.text_for("live_costs")
        finally:
            object.__setattr__(main.config, "db_path", original)

    asyncio.run(go())
