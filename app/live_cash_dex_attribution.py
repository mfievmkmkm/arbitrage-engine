"""Native cash/receipt reconciliation in an existing read-only SQLite snapshot."""

import json
import aiosqlite
from types import SimpleNamespace as NS
from decimal import Decimal, localcontext
from .dex_live_bridge import Plan, dec, wallet_flow
from .dex_cex_backend import rebuild as dex_rebuild, snapshot
from .spot_future_cashflow import rebuild as sf_rebuild
from .spot_spot_live import rebuild as ss_rebuild
from .public_books import normalize
from .recovery_market import executable

USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"


def equal(a, b):
    x, y = dec(a), dec(b)
    return abs(x - y) <= max(Decimal("1e-8"), max(abs(x), abs(y)) * Decimal("1e-8"))


async def intents(db, tid, allow_empty=False):
    db.row_factory = aiosqlite.Row
    cursor = await db.execute(
        "SELECT rowid AS _journal_sequence,* FROM order_intents WHERE trade_id=? ORDER BY rowid LIMIT 20001",
        (tid,),
    )
    rows = [dict(r) for r in await cursor.fetchall()]
    if (not rows and not allow_empty) or len(rows) > 20000:
        raise ValueError("COST_INTENTS_MISSING_OR_LIMITED")
    result = {}
    for r in rows:
        p = json.loads(r["payload"])
        if any(
            p.get(k) != r[k]
            for k in ("intent_id", "trade_id", "venue", "symbol", "side", "state")
        ):
            raise ValueError("COST_INTENT_CONFLICT")
        if (
            dec(p.get("qty")) != dec(r["qty"])
            or type(p.get("reduce_only")) not in (bool, int)
            or p["reduce_only"] not in (0, 1)
            or p["reduce_only"] != r["reduce_only"]
        ):
            raise ValueError("COST_INTENT_CONFLICT")
        result[r["intent_id"]] = dict(p, _journal_sequence=r["_journal_sequence"])
    return rows, result


async def result(db, trade, meta, key="result"):
    if trade["phase"] not in ("CLOSED_PRIVATE_VERIFIED", "CLOSED_WITH_INVENTORY"):
        raise ValueError("COST_CYCLE_NOT_FINALIZED")
    cur = await db.execute(
        "SELECT * FROM live_results WHERE trade_id=?", (trade["trade_id"],)
    )
    row = await cur.fetchone()
    if row is None:
        raise ValueError("COST_RESULT_MISSING")
    stored = json.loads(row["payload"])
    if stored != meta.get(key):
        raise ValueError("COST_DURABLE_RESULT_CONFLICT")
    return dict(row), stored


def checked(row, values):
    if not all(equal(row[k], v) for k, v in values.items()):
        raise ValueError("COST_RESULT_RECONCILIATION_CONFLICT")


async def funding(db, tid, venue, symbol, opened, closed):
    cur = await db.execute("SELECT * FROM funding_settlements WHERE trade_id=?", (tid,))
    total = Decimal(0)
    for raw in await cur.fetchall():
        r = dict(raw)
        e = json.loads(r["payload"])
        if (
            any(
                e.get(k) != r[k]
                for k in ("venue", "event_id", "symbol", "ts", "amount")
            )
            or r["venue"] != venue
            or r["symbol"] != symbol
            or not dec(opened) <= dec(r["ts"]) <= dec(closed)
        ):
            raise ValueError("COST_FUNDING_SCOPE_CONFLICT")
        total += dec(r["amount"])
    return total


async def details(db, rows, base=None):
    from .live_execution_costs import order_cost

    out = []
    for r in rows:
        cur = await db.execute(
            "SELECT * FROM order_request_evidence WHERE intent_id=?", (r["intent_id"],)
        )
        proof = await cur.fetchone()
        if proof is None:
            raise ValueError("COST_REQUEST_MISSING")
        out.append(order_cost(r, dict(proof), base_currency=base))
    return out


async def cash(db, trade, meta):
    tid = trade["trade_id"]
    rows, journal = await intents(db, tid)
    row, stored = await result(db, trade, meta)
    strategy = meta["strategy"]
    inventory_charge = 0
    if strategy == "spot_futures":
        p = NS(**meta["cash_plan"])
        flow = sf_rebuild(
            journal,
            tid,
            p.venue + ":spot",
            p.venue,
            p.spot_symbol,
            p.future_symbol,
            p.base,
            p.contract_size,
        )
        proof = meta.get("cash_close_proof", {})
        if (
            proof.get("future_flat") is not True
            or flow.future_base != 0
            or not equal(proof.get("spot_owned"), flow.spot_base)
            or not equal(proof.get("held_inventory_cost"), flow.spot_cost_basis)
        ):
            raise ValueError("COST_CASH_CLOSE_PROOF_INCOMPLETE")
        funded = await funding(
            db, tid, p.venue, p.future_symbol, p.opened_at, meta["cash_closed_at"]
        )
        evidence = meta.get("cash_funding_evidence", {})
        if (
            evidence.get("verified") is not True
            or not equal(evidence.get("window_start"), p.opened_at)
            or not equal(evidence.get("window_end"), meta["cash_closed_at"])
            or dec(evidence.get("covered_until")) < dec(meta["cash_closed_at"])
            or not equal(evidence.get("amount"), funded)
        ):
            raise ValueError("COST_FUNDING_COVERAGE_MISSING")
        cur = await db.execute(
            "SELECT payload FROM funding_settlements WHERE trade_id=? ORDER BY venue,event_id",
            (tid,),
        )
        archived = [json.loads(r[0]) for r in await cur.fetchall()]
        if sorted(archived, key=lambda e: (e["venue"], e["event_id"])) != sorted(
            evidence.get("events", []), key=lambda e: (e["venue"], e["event_id"])
        ):
            raise ValueError("COST_FUNDING_WITNESS_CONFLICT")
        fees = flow.quote_fees + flow.base_fee_usd
        gross = flow.spot_cash + flow.future_realized + flow.base_fee_usd
        net = flow.net(float(funded), True)
        if not all(
            equal(stored[k], v)
            for k, v in dict(
                quote_fees=flow.quote_fees,
                base_fee_usd=flow.base_fee_usd,
                base_fees=flow.base_fees,
                held_inventory_base=flow.spot_base,
                held_inventory_cost=flow.spot_cost_basis,
                inventory_valuation_in_net=0,
            ).items()
        ):
            raise ValueError("COST_INVENTORY_RESULT_CONFLICT")
        cur = await db.execute(
            "SELECT * FROM live_cash_inventory WHERE trade_id=?", (tid,)
        )
        held = await cur.fetchone()
        if flow.spot_base > 0:
            if (
                held is None
                or (held["venue"], held["base"]) != (p.venue, p.base)
                or not equal(held["qty"], flow.spot_base)
                or not equal(held["cost_usd"], flow.spot_cost_basis)
                or json.loads(held["payload"]) != proof
                or trade["phase"] != "CLOSED_WITH_INVENTORY"
            ):
                raise ValueError("COST_HELD_INVENTORY_CONFLICT")
        elif held is not None or trade["phase"] != "CLOSED_PRIVATE_VERIFIED":
            raise ValueError("COST_HELD_INVENTORY_CONFLICT")
        extra = dict(
            held_inventory_base=flow.spot_base,
            held_inventory_cost=flow.spot_cost_basis,
            inventory_valuation_in_net=0,
            quote_fees=flow.quote_fees,
            base_fee_usd=flow.base_fee_usd,
        )
    else:
        p = NS(**meta["spot_spot_plan"])
        flow = ss_rebuild(journal, tid, p)
        proof = meta.get("close_proof", {})
        if (
            proof.get("verified") is not True
            or proof.get("base_deltas") != flow["base"]
            or proof.get("cash_deltas") != flow["cash"]
        ):
            raise ValueError("COST_CASH_CLOSE_PROOF_INCOMPLETE")
        inventory_charge = sum(max(0, -v) * p.reference for v in flow["base"].values())
        net = sum(flow["cash"].values()) - inventory_charge
        fees = flow["fees"] + flow["base_fee_usd"]
        gross = net + fees
        funded = Decimal(0)
        if stored.get("inventory_deltas") != flow["base"] or not equal(
            stored.get("inventory_deficit_charge"), inventory_charge
        ):
            raise ValueError("COST_INVENTORY_RESULT_CONFLICT")
        expected_phase = (
            "CLOSED_WITH_INVENTORY"
            if any(flow["base"].values())
            else "CLOSED_PRIVATE_VERIFIED"
        )
        if trade["phase"] != expected_phase:
            raise ValueError("COST_INVENTORY_PHASE_CONFLICT")
        cur = await db.execute(
            "SELECT * FROM live_spot_allocations WHERE trade_id=?", (tid,)
        )
        allocations = {r["venue"]: dict(r) for r in await cur.fetchall()}
        if set(allocations) != set(p.venues):
            raise ValueError("COST_ALLOCATION_MISSING")
        for venue, a in allocations.items():
            if a["base"] != p.base or not all(
                equal(a[k], v)
                for k, v in dict(
                    baseline_total=p.baseline[venue][p.base]["total"],
                    delta=flow["base"][venue],
                    deficit_charge=max(0, -flow["base"][venue]) * p.reference,
                ).items()
            ):
                raise ValueError("COST_ALLOCATION_CONFLICT")
        extra = dict(
            inventory_deltas=flow["base"],
            quote_fees=flow["fees"],
            base_fee_usd=flow["base_fee_usd"],
        )
    checked(row, dict(gross=gross, fees=fees, funding=funded, net=net))
    orders = await details(db, rows, p.base)
    return (
        dict(
            status="RECONCILED",
            gross=gross,
            fees=fees,
            trading_fees=fees,
            gas=0,
            funding=float(funded),
            net=net,
            safety_reserve=0,
            inventory_deficit_charge=inventory_charge,
            gross_before_inventory_charge=gross + inventory_charge,
            adverse_slippage_usd=sum(r["adverse_slippage_usd"] for r in orders),
            slippage_complete=True,
            **extra
        ),
        orders,
    )


def dex_costs(p, costs, wallet, closed):
    if (
        p.quote != USDT
        or p.quote_decimals != 6
        or costs.get("quote_usdt_identity_evidence")
        != dict(chain_id=1, token=USDT, decimals=6)
    ):
        raise ValueError("COST_DEX_QUOTE_IDENTITY_INVALID")
    if (
        costs.get("verified") is not True
        or costs.get("venue") != p.venue
        or costs.get("symbol") != p.symbol
        or costs.get("gas_raw") != str(wallet["gas_raw"])
        or costs.get("hashes") != wallet["hashes"]
    ):
        raise ValueError("COST_DEX_SCOPE_CONFLICT")
    gas = costs.get("gas_valuation_evidence")
    if (
        not isinstance(gas, dict)
        or gas.get("method") != "CURRENT_EXECUTABLE_ETH_REPLACEMENT_ASK_NOT_A_FILL"
        or gas.get("native_asset") != "ETH"
        or gas.get("native_raw") != costs["gas_raw"]
        or gas.get("symbol") != "ETH/USDT"
        or not gas.get("venue")
    ):
        raise ValueError("COST_GAS_VALUATION_MISSING")
    b = gas["book"]
    if gas.get("market_identity") != dict(
        spot=True, active=True, base="ETH", quote="USDT"
    ):
        raise ValueError("COST_GAS_MARKET_IDENTITY_INVALID")
    normalized = normalize(
        b,
        "ETH/USDT",
        b["requested_at"],
        b["received_at"],
        1.5,
        b.get("data_source", "REST"),
    )
    native = dec(wallet["gas_raw"]) / 10**18
    if native <= 0:
        raise ValueError("COST_GAS_RAW_INVALID")
    average, worst = executable(normalized["asks"], float(native))
    valued = native * dec(average)
    if (
        not equal(gas["average_price"], average)
        or not equal(gas["worst_price"], worst)
        or abs(dec(costs["gas_usdt"]) - valued)
        > max(Decimal("1e-24"), abs(valued) * Decimal("1e-12"))
    ):
        raise ValueError("COST_GAS_VALUATION_CONFLICT")
    events = costs.get("funding_events")
    if not isinstance(events, list) or dec(costs.get("funding_covered_until")) < dec(
        closed
    ):
        raise ValueError("COST_PRIVATE_FUNDING_MISSING")
    if dec(costs.get("funding_verified_at")) - dec(closed) < 30 or dec(
        b["received_at"]
    ) < dec(closed):
        raise ValueError("COST_PRIVATE_FUNDING_IMMATURE")
    total, seen = Decimal(0), set()
    for e in events:
        key = (e.get("venue"), e.get("event_id"))
        if (
            key in seen
            or not isinstance(key[1], str)
            or not key[1]
            or e.get("venue") != p.venue
            or e.get("symbol") != p.symbol
            or not dec(p.opened_at) <= dec(e.get("ts")) <= dec(closed)
            or e.get("source") != "native_private_income"
        ):
            raise ValueError("COST_PRIVATE_FUNDING_CONFLICT")
        seen.add(key)
        total += dec(e["amount"])
    if not equal(total, costs.get("funding")):
        raise ValueError("COST_PRIVATE_FUNDING_CONFLICT")
    return valued, total


async def dex(db, trade, meta):
    tid = trade["trade_id"]
    p = Plan(**meta["dex_live_plan"])
    p.validate()
    row, stored = await result(db, trade, meta, "dex_result")
    if stored.get("plan") != meta["dex_live_plan"]:
        raise ValueError("COST_DEX_PLAN_CONFLICT")
    cur = await db.execute(
        "SELECT * FROM wallet_tx_intents WHERE trade_id=? ORDER BY nonce LIMIT 20001",
        (tid,),
    )
    receipts = [dict(r) for r in await cur.fetchall()]
    if not receipts or len(receipts) > 20000:
        raise ValueError("COST_WALLET_RECEIPTS_MISSING_OR_LIMITED")
    wallet = wallet_flow(receipts, tid, p)
    last = max(receipts, key=lambda r: r["nonce"])
    final_receipt = json.loads(last["payload"])["receipt"]
    rows, journal = await intents(db, tid, allow_empty=True)
    cex = dex_rebuild(journal, tid, p)
    obs = stored["observation"]
    inventory = obs.get("wallet_inventory", {})
    if (
        inventory.get("verified") is not True
        or inventory.get("wallet") != p.wallet
        or inventory.get("chain_id") != 1
        or inventory.get("nonce") != last["nonce"] + 1
        or inventory.get("balances") != final_receipt["after"]
        or inventory.get("native_raw") != final_receipt["native_after_raw"]
    ):
        raise ValueError("COST_DEX_PRIVATE_INVENTORY_CONFLICT")
    if (
        obs.get("status") != "VERIFIED"
        or obs.get("trade_id") != tid
        or obs.get("wallet") != wallet
        or obs.get("wallet_snapshot")
        != sorted(
            [r["intent_id"], r["phase"], r["tx_hash"], r["payload"]] for r in receipts
        )
        or obs["cex"].get("verified") is not True
        or obs["cex"].get("journal_snapshot") != snapshot(journal)
        or any(
            obs["cex"].get(k) != cex[k] for k in ("base", "realized", "fees", "stages")
        )
    ):
        raise ValueError("COST_DEX_JOURNAL_CONFLICT")
    if (
        trade["phase"] != "CLOSED_PRIVATE_VERIFIED"
        or wallet["asset_raw"] != 0
        or dec(cex["base"]) != 0
    ):
        raise ValueError("COST_DEX_NOT_FLAT")
    costs = stored["costs"]
    if costs.get("trade_id") != tid:
        raise ValueError("COST_DEX_SCOPE_CONFLICT")
    with localcontext() as ctx:
        ctx.prec = 80
        gas, funded = dex_costs(p, costs, wallet, meta["dex_closed_at"])
        gross = dec(wallet["quote_raw"]) / 10**p.quote_decimals + dec(cex["realized"])
        fees = dec(cex["fees"])
        safety = dec(p.safety)
        net = gross - fees - gas + funded - safety
    for k, v in dict(gross=gross, fees=fees, gas=gas, funding=funded, net=net).items():
        if not equal(stored[k], v):
            raise ValueError("COST_DEX_RESULT_CONFLICT")
    checked(row, dict(gross=gross, fees=fees + gas, funding=funded, net=net))
    orders = await details(db, rows)
    from .dex_slippage import attribute

    slippage = attribute(receipts, p)
    cex_slippage = sum(r["adverse_slippage_usd"] for r in orders)
    return (
        dict(
            status="RECONCILED",
            gross=float(gross),
            fees=float(fees + gas),
            trading_fees=float(fees),
            gas=float(gas),
            funding=float(funded),
            net=float(net),
            safety_reserve=float(safety),
            inventory_deficit_charge=0,
            net_before_safety_reserve=float(net + safety),
            gas_raw=str(wallet["gas_raw"]),
            gas_asset="ETH",
            gas_valuation_method=costs["gas_valuation_evidence"]["method"],
            observed_cex_slippage_usd=cex_slippage,
            wallet_adverse_slippage_usd=(
                slippage["adverse_usdt"] if slippage["complete"] else None
            ),
            wallet_favorable_slippage_usd=(
                slippage["favorable_usdt"] if slippage["complete"] else None
            ),
            wallet_slippage_evidence=slippage,
            adverse_slippage_usd=(
                cex_slippage + slippage["adverse_usdt"]
                if slippage["complete"]
                else None
            ),
            slippage_complete=slippage["complete"],
            slippage_reason=(
                "FIRM_PRICE_ATTRIBUTED_NOT_EXTRA_NET_CHARGE"
                if slippage["complete"]
                else "DEX_WALLET_FIRM_REFERENCE_NOT_ATTRIBUTED"
            ),
        ),
        orders,
    )
