"""Atomic cash result + explicitly owned residual inventory, never false-flat."""

import json
import math
import time
import aiosqlite
from .spot_future_cashflow import rebuild

SCHEMA = """CREATE TABLE IF NOT EXISTS live_cash_inventory(
 trade_id TEXT PRIMARY KEY,venue TEXT NOT NULL,base TEXT NOT NULL,
 qty REAL NOT NULL,cost_usd REAL NOT NULL,ts REAL NOT NULL,payload TEXT NOT NULL)"""


async def finalize(path, trade_id, plan, expected_flow, proof, funding, now=None):
    now = time.time() if now is None else now
    if (
        proof.get("future_flat") is not True
        or funding.verified is not True
        or funding.covered_until < plan.closed_at
    ):
        raise ValueError("CASH_RESULT_PROOF_INCOMPLETE")
    if expected_flow.future_base != 0 or expected_flow.spot_cost_basis > 0.05:
        raise ValueError("CASH_RESIDUAL_INVENTORY_LIMIT")
    if (
        proof.get("spot_owned") != expected_flow.spot_base
        or proof.get("held_inventory_cost") != expected_flow.spot_cost_basis
    ):
        raise ValueError("CASH_RESULT_PRIVATE_MISMATCH")
    async with aiosqlite.connect(path) as d:
        await d.execute(SCHEMA)
        await d.execute("BEGIN IMMEDIATE")
        cur = await d.execute(
            "SELECT phase,payload FROM live_trades WHERE trade_id=?", (trade_id,)
        )
        row = await cur.fetchone()
        if row is None:
            raise ValueError("CASH_RESULT_TRADE_MISSING")
        cur = await d.execute(
            "SELECT 1 FROM live_results WHERE trade_id=?", (trade_id,)
        )
        if await cur.fetchone():
            return False
        if row[0] != "CASH_ACCOUNTING_PENDING":
            raise ValueError("CASH_RESULT_PHASE_INVALID")
        payload = json.loads(row[1])
        if (
            payload.get("strategy") != "spot_futures"
            or payload.get("cash_plan") != plan.persisted
        ):
            raise ValueError("CASH_RESULT_SCOPE_MISMATCH")
        cur = await d.execute(
            "SELECT rowid,intent_id,payload FROM order_intents WHERE trade_id=? ORDER BY rowid",
            (trade_id,),
        )
        intents = {}
        for sequence, iid, raw in await cur.fetchall():
            intents[iid] = dict(json.loads(raw), _journal_sequence=sequence)
        flow = rebuild(
            intents,
            trade_id,
            plan.venue + ":spot",
            plan.venue,
            plan.spot_symbol,
            plan.future_symbol,
            plan.base,
            plan.contract_size,
        )
        if flow != expected_flow:
            raise ValueError("CASH_RESULT_JOURNAL_CHANGED")
        cur = await d.execute(
            "SELECT COALESCE(SUM(cost_usd),0) FROM live_cash_inventory"
        )
        held = float((await cur.fetchone())[0])
        if held + flow.spot_cost_basis > 0.5:
            raise ValueError("CASH_TOTAL_INVENTORY_LIMIT")
        total = 0
        seen = set()
        for e in funding.events:
            key = (e["venue"], e["event_id"])
            if (
                key in seen
                or e["venue"] != plan.venue
                or e["symbol"] != plan.future_symbol
                or not plan.opened_at <= e["ts"] <= plan.closed_at
            ):
                raise ValueError("CASH_FUNDING_EVENT_SCOPE_INVALID")
            seen.add(key)
            total += e["amount"]
            cur = await d.execute(
                "SELECT trade_id,payload FROM funding_settlements WHERE venue=? AND event_id=?",
                key,
            )
            old = await cur.fetchone()
            if old and (old[0] != trade_id or json.loads(old[1]) != e):
                raise ValueError("CASH_FUNDING_EVENT_ALREADY_ATTRIBUTED")
            await d.execute(
                "INSERT OR IGNORE INTO funding_settlements VALUES(?,?,?,?,?,?,?)",
                (*key, trade_id, e["symbol"], e["ts"], e["amount"], json.dumps(e)),
            )
        if not math.isfinite(total) or not math.isclose(
            total, funding.amount, abs_tol=1e-12, rel_tol=1e-12
        ):
            raise ValueError("CASH_FUNDING_TOTAL_CONFLICT")
        net = flow.net(funding.amount, True)
        result = dict(
            trade_id=trade_id,
            strategy="spot_futures",
            # Base fees are already embedded in cash/inventory losses.
            # Add them back to gross before subtracting total fees once.
            gross=flow.spot_cash + flow.future_realized + flow.base_fee_usd,
            fees=flow.quote_fees + flow.base_fee_usd,
            funding=funding.amount,
            net=net,
            reason=plan.reason,
            base_fees=flow.base_fees,
            held_inventory_base=flow.spot_base,
            quote_fees=flow.quote_fees,
            base_fee_usd=flow.base_fee_usd,
            fee_conversion_basis="ACTUAL_SPOT_FILL_PRICE",
            held_inventory_cost=flow.spot_cost_basis,
            inventory_valuation_in_net=0,
            private_flat=flow.spot_base == 0,
        )
        if not all(
            math.isfinite(result[k])
            for k in ("gross", "fees", "funding", "net", "held_inventory_cost")
        ):
            raise ValueError("CASH_RESULT_NONFINITE")
        phase = (
            "CLOSED_WITH_INVENTORY" if flow.spot_base > 0 else "CLOSED_PRIVATE_VERIFIED"
        )
        if flow.spot_base > 0:
            await d.execute(
                "INSERT INTO live_cash_inventory VALUES(?,?,?,?,?,?,?)",
                (
                    trade_id,
                    plan.venue,
                    plan.base,
                    flow.spot_base,
                    flow.spot_cost_basis,
                    now,
                    json.dumps(proof),
                ),
            )
        await d.execute(
            "INSERT INTO live_results VALUES(?,?,?,?,?,?,?,?)",
            (
                trade_id,
                now,
                result["gross"],
                result["fees"],
                funding.amount,
                net,
                plan.reason,
                json.dumps(result),
            ),
        )
        payload.update(
            result=result,
            cash_close_proof=proof,
            cash_funding_evidence=dict(
                verified=True,
                covered_until=funding.covered_until,
                window_start=plan.opened_at,
                window_end=plan.closed_at,
                amount=funding.amount,
                events=list(funding.events),
            ),
        )
        await d.execute(
            "UPDATE live_trades SET phase=?,updated_at=?,payload=? WHERE trade_id=?",
            (phase, now, json.dumps(payload), trade_id),
        )
        for kind in ("TRADE_RESULT", "STATE"):
            event = dict(
                trade_id=trade_id,
                ts=now,
                kind=kind,
                phase=phase,
                result=result,
                proof=proof,
            )
            await d.execute(
                "INSERT INTO execution_events(trade_id,ts,kind,reason,payload) VALUES(?,?,?,?,?)",
                (trade_id, now, kind, plan.reason, json.dumps(event)),
            )
        await d.commit()
        return True
