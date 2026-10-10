import asyncio
import copy
import json
from types import SimpleNamespace as NS

import aiosqlite
import pytest

from app.dex_slippage import attribute, reference, validate, seal

ASSET = "0x" + "1" * 40
USDT = "0x" + "2" * 40
PLAN = NS(asset=ASSET, quote=USDT, asset_decimals=2, quote_decimals=6)


def row(
    *,
    buying=True,
    mode="exact_in",
    expected_sell=None,
    actual_sell=None,
    actual_buy=None
):
    sell, buy = (USDT, ASSET) if buying else (ASSET, USDT)
    expected_sell = (
        expected_sell if expected_sell is not None else (4000000 if buying else 100)
    )
    expected_buy = 100 if buying else 4000000
    maximum = expected_sell * 2 if mode == "exact_out" else expected_sell
    q = dict(
        quote_fingerprint="a" * 64,
        chain_id=1,
        sell_token=sell,
        buy_token=buy,
        sell_amount_raw=str(maximum),
        max_sell_amount_raw=str(maximum) if mode == "exact_out" else None,
        buy_amount_raw=str(expected_buy),
        quote_mode=mode,
        ts=100,
        received_at=101,
    )
    q["execution_reference"] = reference(
        q, expected_sell, 6 if buying else 2, 2 if buying else 6
    )
    sold = actual_sell if actual_sell is not None else expected_sell
    bought = actual_buy if actual_buy is not None else expected_buy
    return dict(
        intent_id="t:wallet:1",
        created_at=101,
        phase="FINALIZED_SUCCESS",
        payload=json.dumps(
            dict(
                proof=q, receipt=dict(token_deltas={sell: str(-sold), buy: str(bought)})
            )
        ),
    )


@pytest.mark.parametrize(
    "buying,mode,sold,bought,expected",
    [
        (True, "exact_in", 4000000, 90, 0.4),
        (True, "exact_in", 4000000, 110, -0.4),
        (False, "exact_in", 100, 3900000, 0.1),
        (False, "exact_in", 100, 4100000, -0.1),
        (True, "exact_out", 4200000, 100, 0.2),
        (True, "exact_out", 3800000, 100, -0.2),
        (False, "exact_out", 105, 4000000, 0.2),
        (False, "exact_out", 95, 4000000, -0.2),
    ],
)
def test_exact_in_out_both_directions_native_units(
    buying, mode, sold, bought, expected
):
    report = attribute(
        [row(buying=buying, mode=mode, actual_sell=sold, actual_buy=bought)], PLAN
    )
    assert report["complete"], report
    assert report["results"][0]["signed_deviation_usdt"] == pytest.approx(expected)
    assert report["adverse_usdt"] == pytest.approx(max(0, expected))
    assert report["favorable_usdt"] == pytest.approx(max(0, -expected))


def test_max_input_is_not_used_as_expected_exact_out_price():
    report = attribute([row(mode="exact_out", actual_sell=4000000)], PLAN)
    assert (
        report["complete"] and report["adverse_usdt"] == report["favorable_usdt"] == 0
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("sha256", "0" * 64),
        ("expected_sell_raw", "0"),
        ("expected_buy_raw", "1000"),
        ("sell_decimals", 18),
        ("sell_token", ASSET),
        ("quote_fingerprint", "b" * 64),
        ("quote_mode", "exact_out"),
        ("source", "MAX_INPUT_IS_EXPECTED_PRICE"),
    ],
)
def test_mutated_reference_never_becomes_zero_slippage(field, value):
    r = row()
    payload = json.loads(r["payload"])
    payload["proof"]["execution_reference"][field] = value
    r["payload"] = json.dumps(payload)
    report = attribute([r], PLAN)
    assert not report["complete"] and report["missing"]


def test_even_resealed_wrong_decimal_identity_is_rejected():
    r = row()
    p = json.loads(r["payload"])
    ref = p["proof"]["execution_reference"]
    ref["sell_decimals"] = 18
    ref["sha256"] = seal({k: v for k, v in ref.items() if k != "sha256"})
    r["payload"] = json.dumps(p)
    assert not attribute([r], PLAN)["complete"]


def test_legacy_missing_reference_and_exact_out_missing_expected_sell_explicit():
    r = row(mode="exact_out")
    p = json.loads(r["payload"])
    assert reference(p["proof"], None, 6, 2) is None
    p["proof"].pop("execution_reference")
    r["payload"] = json.dumps(p)
    report = attribute([r], PLAN)
    assert not report["complete"]
    assert report["missing"][0]["reason"] == "DEX_FIRM_EXPECTED_PRICE_MISSING"


def test_revert_has_no_swap_slippage_not_zero_gas():
    r = row()
    r["phase"] = "FINALIZED_REVERT"
    p = json.loads(r["payload"])
    p["receipt"]["token_deltas"] = {k: "0" for k in p["receipt"]["token_deltas"]}
    r["payload"] = json.dumps(p)
    report = attribute([r], PLAN)
    assert report["complete"] and report["results"][0]["source"] == "REVERT_NO_SWAP"
    assert report["adverse_usdt"] == 0


def test_reference_after_or_too_old_at_claim_cannot_be_used():
    r = row()
    r["created_at"] = 99
    assert not attribute([r], PLAN)["complete"]
    r["created_at"] = 200
    assert not attribute([r], PLAN)["complete"]


def test_revert_with_token_movement_is_not_zero_slippage():
    r = row()
    r["phase"] = "FINALIZED_REVERT"
    assert not attribute([r], PLAN)["complete"]


@pytest.mark.parametrize("direction", ["forward", "reverse"])
def test_full_native_cost_cycle_attributed_without_changing_actual_net(
    tmp_path, direction
):
    async def run():
        from tests.test_cross_strategy_costs import dex_closed
        from app.live_execution_costs import build, render

        store, _ = await dex_closed(tmp_path, direction)
        before = (await build(store.path))["trades"][0]
        async with aiosqlite.connect(store.path) as d:
            d.row_factory = aiosqlite.Row
            records = await (
                await d.execute("SELECT * FROM wallet_tx_intents ORDER BY nonce")
            ).fetchall()
            for r in records:
                payload = json.loads(r["payload"])
                q, receipt = payload["proof"], payload["receipt"]
                sell, buy = q["sell_token"], q["buy_token"]
                sold = -int(receipt["token_deltas"][sell])
                bought = int(receipt["token_deltas"][buy])
                q.update(
                    chain_id=1,
                    quote_fingerprint="a" * 64,
                    sell_amount_raw=str(sold),
                    buy_amount_raw=str(bought),
                    quote_mode="exact_in",
                    ts=r["created_at"],
                    received_at=r["created_at"],
                )
                q["execution_reference"] = reference(
                    q,
                    sold,
                    (
                        6
                        if sell
                        == q.get(
                            "quote_token", "0xdac17f958d2ee523a2206206994597c13d831ec7"
                        )
                        else 2
                    ),
                    6 if buy == "0xdac17f958d2ee523a2206206994597c13d831ec7" else 2,
                )
                # Fixture's Plan asset decimals are 2; preserve raw journal units.
                text = json.dumps(payload)
                await d.execute(
                    "UPDATE wallet_tx_intents SET payload=? WHERE intent_id=?",
                    (text, r["intent_id"]),
                )
            fresh = await (
                await d.execute(
                    "SELECT intent_id,phase,tx_hash,payload FROM wallet_tx_intents"
                )
            ).fetchall()
            result_row = await (
                await d.execute("SELECT payload FROM live_results WHERE trade_id='t'")
            ).fetchone()
            result = json.loads(result_row[0])
            result["observation"]["wallet_snapshot"] = sorted(
                [x["intent_id"], x["phase"], x["tx_hash"], x["payload"]] for x in fresh
            )
            await d.execute(
                "UPDATE live_results SET payload=? WHERE trade_id='t'",
                (json.dumps(result),),
            )
            meta_row = await (
                await d.execute("SELECT payload FROM live_trades WHERE trade_id='t'")
            ).fetchone()
            meta = json.loads(meta_row[0])
            meta["dex_result"] = result
            await d.execute(
                "UPDATE live_trades SET payload=? WHERE trade_id='t'",
                (json.dumps(meta),),
            )
            await d.commit()
        report = await build(store.path)
        actual = report["trades"][0]
        assert actual["status"] == "RECONCILED" and actual["slippage_complete"], actual
        assert actual["wallet_adverse_slippage_usd"] == 0
        assert actual["net"] == before["net"]
        assert "DEX отклонение от firm-цены" in render(report)

    asyncio.run(run())
