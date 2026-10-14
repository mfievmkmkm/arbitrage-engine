"""Weighted-average reduction basis and complete cashflow from terminal fills."""

import math
from types import SimpleNamespace
from .live_recovery_evidence import Unverified
from .durable_order_reconcile import TERMINAL
from .trade_result import TradeResult


def finite(value, positive=False):
    try:
        if isinstance(value, bool):
            raise ValueError()
        v = float(value)
        if not math.isfinite(v) or (positive and v <= 0):
            raise ValueError()
        return v
    except (ValueError, TypeError):
        raise Unverified("REDUCED_FILL_ACCOUNTING_INVALID")


def reduction(x, tid):
    return bool(x["reduce_only"]) and x["intent_id"].startswith(
        tid + ":entry-recovery:"
    )


def history(row, payload, intents):
    tid, symbol, lv, sv = (
        row[k] for k in ("trade_id", "symbol", "long_venue", "short_venue")
    )
    if not tid or lv == sv or not intents:
        raise Unverified("REDUCED_FILL_SCOPE_INVALID")
    sizes = [
        finite(payload.get(k), True)
        for k in ("long_contract_size", "short_contract_size")
    ]
    opened = finite(payload.get("opened_at"), True)
    totals = [
        dict(
            entry_qty=0.0,
            entry_cost=0.0,
            entry_fee=0.0,
            close_qty=0.0,
            close_cost=0.0,
            close_fee=0.0,
            reduced_qty=0.0,
            reduced_cost=0.0,
            reduced_fee=0.0,
        )
        for _ in sizes
    ]
    ids, orders = set(), set()
    for x in intents:
        iid = x.get("intent_id")
        if not isinstance(iid, str) or iid in ids:
            raise Unverified("REDUCED_DUPLICATE_INTENT")
        ids.add(iid)
        if (
            x.get("trade_id") != tid
            or x.get("symbol") != symbol
            or x.get("venue") not in (lv, sv)
        ):
            raise Unverified("REDUCED_FILL_SCOPE_INVALID")
        if x.get("state") not in TERMINAL:
            raise Unverified("ORDER_EVIDENCE_INCOMPLETE")
        flag = x.get("reduce_only")
        if type(flag) not in (bool, int) or flag not in (0, 1):
            raise Unverified("REDUCED_ORDER_FLAG_INVALID")
        idx = 0 if x["venue"] == lv else 1
        expected = (
            ("sell" if idx == 0 else "buy") if flag else ("buy" if idx == 0 else "sell")
        )
        if x.get("side") != expected:
            raise Unverified("UNEXPECTED_FILL_DIRECTION")
        qty = finite(x.get("filled"))
        target = finite(x.get("qty"), True)
        fee = finite(x.get("fee"))
        if qty < 0 or qty > target * (1 + 1e-10):
            raise Unverified("REDUCED_FILL_QUANTITY_INVALID")
        price = finite(x.get("avg_price"), True) if qty else 0
        if qty:
            oid = x.get("order_id")
            if not oid or (x["venue"], oid) in orders:
                raise Unverified("REDUCED_DUPLICATE_OR_MISSING_ORDER")
            orders.add((x["venue"], oid))
        t = totals[idx]
        prefix = "close" if flag else "entry"
        t[prefix + "_qty"] += qty
        t[prefix + "_cost"] += qty * price
        t[prefix + "_fee"] += fee
        if reduction(x, tid):
            if iid != f"{tid}:entry-recovery:{x['venue']}:{x['side']}":
                raise Unverified("REDUCED_RECOVERY_ID_INVALID")
            t["reduced_qty"] += qty
            t["reduced_cost"] += qty * price
            t["reduced_fee"] += fee
    if not any(t["entry_qty"] > 0 for t in totals):
        raise Unverified("HEDGE_FILL_MISSING")
    for t in totals:
        if not all(math.isfinite(v) for v in t.values()):
            raise Unverified("REDUCED_FILL_ACCOUNTING_INVALID")
        if t["close_qty"] > t["entry_qty"] * (1 + 1e-10):
            raise Unverified("REDUCED_CLOSE_OVERFILL")
        t["entry_price"] = t["entry_cost"] / t["entry_qty"] if t["entry_qty"] else None
    return sizes, opened, totals


def basis(row, payload, intents):
    sizes, opened, totals = history(row, payload, intents)
    bases = [t["entry_qty"] * s for t, s in zip(totals, sizes)]
    for v in bases:
        finite(v)
    big = 0 if bases[0] > bases[1] else 1
    small = 1 - big
    reduced = [t["reduced_qty"] * s for t, s in zip(totals, sizes)]
    if (
        reduced[small] > 0
        or reduced[big] > abs(bases[0] - bases[1]) + max(bases) * 1e-10
    ):
        raise Unverified("REDUCTION_EXCEEDS_ENTRY_EXCESS")
    gross = sum(
        (t["reduced_cost"] - t["reduced_qty"] * (t["entry_price"] or 0))
        * s
        * (1 if idx == 0 else -1)
        for idx, (t, s) in enumerate(zip(totals, sizes))
    )
    finite(gross)
    prices = [t["entry_price"] for t in totals]
    capital = max(t["entry_cost"] * s for t, s in zip(totals, sizes))
    finite(capital, True)
    return dict(
        kind="TERMINAL_FILL_WEIGHTED_AVERAGE_BASIS",
        remaining_long=max(0, totals[0]["entry_qty"] - totals[0]["reduced_qty"]),
        remaining_short=max(0, totals[1]["entry_qty"] - totals[1]["reduced_qty"]),
        long_entry=prices[0],
        short_entry=prices[1],
        entry_fees=sum(t["entry_fee"] for t in totals),
        recovery_gross=gross,
        recovery_fees=sum(t["reduced_fee"] for t in totals),
        capital=capital,
        opened_at=opened,
        long_size=sizes[0],
        short_size=sizes[1],
    )


def check_runtime(trade, b):
    expected = dict(
        long_contracts=b["remaining_long"],
        short_contracts=b["remaining_short"],
        long_entry=b["long_entry"],
        short_entry=b["short_entry"],
        entry_fees=b["entry_fees"],
        recovery_gross=b["recovery_gross"],
        recovery_fees=b["recovery_fees"],
        recovery_capital=b["capital"],
        opened_at=b["opened_at"],
    )
    if any(
        not math.isclose(finite(getattr(trade, k)), finite(v), rel_tol=1e-10, abs_tol=0)
        for k, v in expected.items()
    ):
        raise Unverified("REDUCED_RUNTIME_ACCOUNTING_MISMATCH")


def cycle(row, payload, intents):
    sizes, opened, totals = history(row, payload, intents)
    if any(
        not math.isclose(t["entry_qty"], t["close_qty"], rel_tol=1e-10, abs_tol=0)
        for t in totals
    ):
        raise Unverified("EXIT_FILL_QUANTITY_MISMATCH")
    gross = sum(
        (t["close_cost"] - t["entry_cost"]) * s * (1 if idx == 0 else -1)
        for idx, (t, s) in enumerate(zip(totals, sizes))
    )
    fees = sum(t["entry_fee"] + t["close_fee"] for t in totals)
    finite(gross)
    finite(fees)
    capital = basis(row, payload, intents)["capital"]
    context = SimpleNamespace(
        trade_id=row["trade_id"],
        symbol=row["symbol"],
        long_venue=row["long_venue"],
        short_venue=row["short_venue"],
        opened_at=opened,
    )

    def finalize(funding):
        funding = finite(funding)
        net = finite(gross - fees + funding)
        return TradeResult(
            row["trade_id"],
            gross,
            fees,
            funding,
            net,
            finite(net / capital * 100) if capital else 0,
            "REDUCED_CYCLE_PRIVATE_VERIFIED_CLOSE",
        )

    return context, finalize
