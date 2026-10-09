"""Exact terminal cashflow and native private exposure for a residual close."""

import hashlib
import json
import math
from dataclasses import dataclass
from .live_recovery_evidence import Unverified, validate_trade
from .reduced_fill_accounting import history, basis, check_runtime, reduction

FIELDS = (
    "intent_id",
    "trade_id",
    "venue",
    "symbol",
    "side",
    "qty",
    "reduce_only",
    "state",
    "order_id",
    "filled",
    "avg_price",
    "fee",
)


def fingerprint(intents):
    rows = [{k: x.get(k) for k in FIELDS} for x in intents]
    rows.sort(key=lambda x: x["intent_id"])
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


@dataclass(frozen=True)
class Residual:
    long_contracts: float
    short_contracts: float
    closed_long: float
    closed_short: float
    fingerprint: str


def evaluate(trade, row, intents, snapshot, now, max_age=15):
    validate_trade(trade)
    payload = json.loads(row["payload"])
    if payload.get("exit_dispatch_status") not in (
        "EXIT_RESIDUAL_PENDING_RECONCILIATION",
        "RESIDUAL_FILLS_PENDING_PRIVATE",
    ):
        raise Unverified("RESIDUAL_INITIAL_DISPATCH_NOT_FINISHED")
    by_id = {x["intent_id"]: x for x in intents}
    for name, venue in (
        ("exit-long", trade.long_venue),
        ("exit-short", trade.short_venue),
    ):
        initial = by_id.get(f"{trade.trade_id}:{name}")
        if (
            not initial
            or not initial.get("reduce_only")
            or initial.get("venue") != venue
        ):
            raise Unverified("RESIDUAL_INITIAL_INTENT_MISSING")
    if (
        row["phase"] != "EXIT_SUBMITTING"
        or row["trade_id"] != trade.trade_id
        or payload.get("runtime_trade") != trade.row()
        or (row["symbol"], row["long_venue"], row["short_venue"])
        != (trade.symbol, trade.long_venue, trade.short_venue)
    ):
        raise Unverified("RESIDUAL_DURABLE_SCOPE_MISMATCH")
    sizes, _, totals = history(row, payload, intents)
    for actual, expected in zip(
        sizes, (trade.long_contract_size, trade.short_contract_size)
    ):
        if not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=0):
            raise Unverified("RESIDUAL_CONTRACT_SIZE_MISMATCH")
    if any(reduction(x, trade.trade_id) for x in intents):
        check_runtime(trade, basis(row, payload, intents))
    else:
        values = (trade.long_contracts, trade.short_contracts)
        prices = (trade.long_entry, trade.short_entry)
        if (
            trade.recovery_gross
            or trade.recovery_fees
            or trade.recovery_capital
            or any(
                not math.isclose(t["entry_qty"], q, rel_tol=1e-10, abs_tol=0)
                or not math.isclose(t["entry_price"], p, rel_tol=1e-10, abs_tol=0)
                for t, q, p in zip(totals, values, prices)
            )
            or not math.isclose(
                sum(t["entry_fee"] for t in totals),
                trade.entry_fees,
                rel_tol=1e-10,
                abs_tol=0,
            )
        ):
            raise Unverified("RESIDUAL_ENTRY_ACCOUNTING_MISMATCH")
    remaining = [max(0, t["entry_qty"] - t["close_qty"]) for t in totals]
    for venue, side, size, expected in zip(
        (trade.long_venue, trade.short_venue), ("long", "short"), sizes, remaining
    ):
        data = snapshot.get(venue, {})
        if not getattr(data.get("health"), "ok", False):
            raise Unverified("RESIDUAL_PRIVATE_UNTRUSTED")
        start, received = data.get("snapshot_started_at"), data.get("fetched_at")
        if (
            any(
                isinstance(v, bool)
                or not isinstance(v, (float, int))
                or not math.isfinite(v)
                for v in (start, received, now)
            )
            or not start <= received <= now
            or now - start > max_age
        ):
            raise Unverified("RESIDUAL_PRIVATE_STALE")
        if not isinstance(data.get("orders"), list) or data["orders"]:
            raise Unverified("RESIDUAL_WORKING_ORDER")
        if not isinstance(data.get("positions"), list):
            raise Unverified("RESIDUAL_PRIVATE_UNTRUSTED")
        actual = 0.0
        for p in data["positions"]:
            if (
                getattr(p, "venue", None) != venue
                or p.symbol != trade.symbol
                or p.side != side
            ):
                raise Unverified("RESIDUAL_PRIVATE_SCOPE_MISMATCH")
            q, base, native_size = p.contracts, p.qty, p.contract_size
            if any(
                isinstance(v, bool)
                or v is None
                or not math.isfinite(float(v))
                or float(v) < 0
                for v in (q, base, native_size)
            ):
                raise Unverified("RESIDUAL_PRIVATE_UNITS_INVALID")
            if not math.isclose(
                float(native_size), size, rel_tol=1e-12, abs_tol=0
            ) or not math.isclose(
                float(q) * size, float(base), rel_tol=1e-10, abs_tol=0
            ):
                raise Unverified("RESIDUAL_PRIVATE_UNITS_INVALID")
            actual += float(q)
        if not math.isclose(actual, expected, rel_tol=1e-8, abs_tol=0):
            raise Unverified("RESIDUAL_PRIVATE_FILL_MISMATCH")
    return Residual(*remaining, *(t["close_qty"] for t in totals), fingerprint(intents))
