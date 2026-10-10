"""Read-only derivative execution attribution, never an accounting authority.

Cumulative order-intent fills are counted once. Slippage explains the difference
from saved public VWAP; it is NOT deducted again from already actual-price NET.
Unsupported cash/DEX and incomplete legacy journals remain explicitly partial.
"""

import json
import math
from html import escape
from pathlib import Path
from urllib.parse import quote

import aiosqlite
from .exchange_executor import SubmitRequest, SubmitResult
from .order_settlement import valid, terminal
from .quote_order_evidence import validate
from .recovery_market import executable, levels

SOURCES = {"PUBLIC_IOC_ENTRY_V1", "PUBLIC_MARKET_ENTRY_V1", "PUBLIC_REST_RECOVERY_V1"}


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("COST_NUMBER_INVALID")
    return float(value)


def close(a, b):
    return math.isclose(a, b, rel_tol=1e-8, abs_tol=1e-8)


def order_cost(row, proof):
    p = json.loads(row["payload"])
    req = SubmitRequest(**json.loads(proof["payload"]))
    e = req.market_evidence
    if not isinstance(e, dict) or e.get("source") not in SOURCES:
        raise ValueError("COST_SCOPE_NOT_SUPPORTED")
    # Historical proofs are checked at their durable claim, not today's clock.
    validate(req, row["venue"], number(proof["ts"]))
    keys = ("trade_id", "venue", "symbol", "side", "qty", "reduce_only", "state")
    if any(p.get(k) != row[k] for k in keys) or p.get("intent_id") != row["intent_id"]:
        raise ValueError("COST_INTENT_CONFLICT")
    if proof["trade_id"] != row["trade_id"] or (
        req.symbol,
        req.side,
        req.qty,
        req.reduce_only,
        req.client_order_id,
    ) != (
        row["symbol"],
        row["side"],
        row["qty"],
        bool(row["reduce_only"]),
        row["intent_id"],
    ):
        raise ValueError("COST_REQUEST_CONFLICT")
    result = SubmitResult(
        p.get("order_id", ""),
        p.get("exchange_status", ""),
        number(p.get("filled")),
        p.get("avg_price"),
        number(p.get("fee")),
    )
    if (
        row["state"] not in {"FILLED", "CANCELED", "CANCELLED", "REJECTED", "EXPIRED"}
        or not valid(result, req.qty)
        or not terminal(result, req.qty)
    ):
        raise ValueError("COST_ORDER_NOT_SETTLED")
    if number(p.get("base_fee", 0)) != 0:
        raise ValueError("COST_BASE_FEE_NOT_SUPPORTED")
    if result.filled > 0 and not result.order_id:
        raise ValueError("COST_ORDER_ID_MISSING")
    size = number(e["contract_size"])
    base = result.filled * size
    average = number(result.avg_price) if base else None
    reference = None
    if base:
        side = "asks" if req.side == "buy" else "bids"
        reference, _ = executable(levels(e[side], side), result.filled)
    cash_sign = -1 if req.side == "buy" else 1
    cash = cash_sign * base * (average or 0)
    reference_cash = cash_sign * base * (reference or 0)
    return dict(
        intent_id=row["intent_id"],
        order_id=result.order_id,
        trade_id=row["trade_id"],
        venue=row["venue"],
        symbol=req.symbol,
        side=req.side,
        order_type=req.order_type,
        reduce_only=req.reduce_only,
        contracts=result.filled,
        contract_size=size,
        base_qty=base,
        avg_price=average,
        reference_vwap=reference,
        actual_cashflow=cash,
        reference_cashflow=reference_cash,
        fees=result.fee,
        adverse_slippage_usd=reference_cash - cash,
        source=e["source"],
        book_ts=e["book_ts"],
    )


async def read(db, limit=100):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("COST_REPORT_LIMIT_INVALID")
    db.row_factory = aiosqlite.Row
    async with db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ) as cursor:
        tables = {r[0] for r in await cursor.fetchall()}
    required = {
        "live_trades",
        "live_results",
        "order_intents",
        "order_request_evidence",
        "funding_settlements",
    }
    if not required <= tables:
        return dict(status="NO_DATA", trades=[], orders=[], execution_authority=False)
    async with db.execute(
        "SELECT * FROM live_trades ORDER BY updated_at DESC,trade_id LIMIT ?",
        (limit + 1,),
    ) as cursor:
        trades = [dict(r) for r in await cursor.fetchall()]
    output, orders = [], []
    for trade in trades[:limit]:
        item = dict(
            trade_id=trade["trade_id"],
            phase=trade["phase"],
            status="PARTIAL",
            reasons=[],
            orders=0,
        )
        try:
            metadata = json.loads(trade["payload"])
            strategy = metadata.get("strategy", "futures_futures")
            item["strategy"] = strategy
            if strategy not in ("futures_futures", "funding_arb"):
                raise ValueError("COST_STRATEGY_NOT_SUPPORTED")
            async with db.execute(
                "SELECT * FROM order_intents WHERE trade_id=? ORDER BY rowid LIMIT 20001",
                (trade["trade_id"],),
            ) as cursor:
                intents = [dict(r) for r in await cursor.fetchall()]
            if not intents or len(intents) > 20000:
                raise ValueError("COST_INTENTS_MISSING_OR_LIMITED")
            details = []
            for intent in intents:
                try:
                    async with db.execute(
                        "SELECT * FROM order_request_evidence WHERE intent_id=?",
                        (intent["intent_id"],),
                    ) as cursor:
                        proof = await cursor.fetchone()
                    if proof is None:
                        raise ValueError("COST_REQUEST_MISSING")
                    details.append(order_cost(intent, dict(proof)))
                except (ValueError, TypeError, KeyError, AttributeError) as error:
                    item["reasons"].append(
                        str(error)
                        if isinstance(error, ValueError)
                        else "COST_EVIDENCE_INVALID"
                    )
            orders.extend(details)
            ids = [(r["venue"], r["order_id"]) for r in details if r["contracts"] > 0]
            if len(set(ids)) != len(ids):
                item["reasons"].append("COST_DUPLICATE_EXCHANGE_ORDER")
            item["orders"] = len(details)
            item["observed_fees"] = sum(r["fees"] for r in details)
            item["observed_adverse_slippage_usd"] = sum(
                r["adverse_slippage_usd"] for r in details
            )
            async with db.execute(
                "SELECT * FROM live_results WHERE trade_id=?", (trade["trade_id"],)
            ) as cursor:
                result = await cursor.fetchone()
            if result is None or trade["phase"] != "CLOSED_PRIVATE_VERIFIED":
                item["reasons"].append("COST_CYCLE_NOT_FINALIZED")
            else:
                proof = metadata.get("close_proof", {})
                if any(
                    proof.get(k) is not True
                    for k in ("private_flat", "orders_terminal", "funding_verified")
                ):
                    item["reasons"].append("COST_CLOSE_PROOF_INCOMPLETE")
                if not item["reasons"]:
                    for field in ("gross", "fees", "funding", "net"):
                        if not close(
                            number(result[field]),
                            number(metadata.get("result", {}).get(field)),
                        ):
                            raise ValueError("COST_DURABLE_RESULT_CONFLICT")
                    funding = number(result["funding"])
                    async with db.execute(
                        "SELECT amount FROM funding_settlements WHERE trade_id=?",
                        (trade["trade_id"],),
                    ) as cursor:
                        funded = sum(number(r[0]) for r in await cursor.fetchall())
                    gross = sum(r["actual_cashflow"] for r in details)
                    fees = item["observed_fees"]
                    net = gross - fees + funded
                    exposure = {}
                    for r in details:
                        key = (r["venue"], r["symbol"])
                        exposure[key] = exposure.get(key, 0) + r["base_qty"] * (
                            1 if r["side"] == "buy" else -1
                        )
                    if any(not close(v, 0) for v in exposure.values()):
                        item["reasons"].append("COST_NATIVE_FLOW_NOT_FLAT")
                    if not all(
                        close(a, number(b))
                        for a, b in (
                            (gross, result["gross"]),
                            (fees, result["fees"]),
                            (funded, funding),
                            (net, result["net"]),
                        )
                    ):
                        item["reasons"].append("COST_RESULT_RECONCILIATION_CONFLICT")
                    if not item["reasons"]:
                        item.update(
                            status="RECONCILED",
                            gross=gross,
                            fees=fees,
                            funding=funded,
                            net=net,
                            reference_gross=sum(
                                r["reference_cashflow"] for r in details
                            ),
                            adverse_slippage_usd=item["observed_adverse_slippage_usd"],
                        )
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            item["reasons"].append(
                str(error) if isinstance(error, ValueError) else "COST_EVIDENCE_INVALID"
            )
        output.append(item)
    return dict(
        status="READ_ONLY",
        trades=output,
        orders=orders,
        capped=len(trades) > limit,
        execution_authority=False,
        slippage_is_explanatory=True,
        scope="FUTURES_FUTURES_AND_FUNDING_NATIVE_CUMULATIVE_INTENTS",
    )


async def build(path, limit=100):
    uri = "file:" + quote(str(Path(path).resolve()), safe="/") + "?mode=ro"
    async with aiosqlite.connect(uri, uri=True) as db:
        await db.execute("BEGIN")
        report = await read(db, limit)
        await db.rollback()
        return report


def render(report):
    trades = report["trades"]
    good = [r for r in trades if r["status"] == "RECONCILED"]
    out = [
        "🧾 <b>Фактические расходы LIVE</b>",
        "Только чтение журнала. Futures/Futures и Funding; cash/DEX — отдельный учёт.",
        f"Сверено: {len(good)} / {len(trades)} циклов",
        f"Комиссии сверенных: {sum(r['fees'] for r in good):.6f} USDT",
        f"Funding сверенных: {sum(r['funding'] for r in good):+.6f} USDT",
        f"NET сверенных: {sum(r['net'] for r in good):+.6f} USDT",
        "Slippage объясняет разницу с сохранённым public VWAP и не вычитается из NET повторно.",
    ]
    if not trades:
        out.append("Подходящих durable циклов пока нет.")
    for row in trades[:6]:
        out.append(
            f"<code>{escape(str(row['trade_id'])[:48])}</code> · {row['status']}"
        )
        if row["reasons"]:
            out.append(escape(" / ".join(row["reasons"][:2])[:180]))
    if report.get("capped"):
        out.append("Выборка ограничена последними циклами; это не полный итог счёта.")
    return "\n".join(out)
