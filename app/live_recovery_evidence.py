"""Recover positions exclusively from terminal fill evidence and private exposure."""

import math
from .runtime_state import RuntimeTrade
from .durable_order_reconcile import TERMINAL
from .runtime_private_reconcile import verify_trade


class Unverified(ValueError):
    pass


def leg_fills(intents, venue, side, reduce_only, symbol):
    selected = [
        x
        for x in intents
        if x["venue"] == venue
        and x["symbol"] == symbol
        and x["side"] == side
        and bool(x["reduce_only"]) == reduce_only
        and float(x.get("filled") or 0) > 0
    ]
    qty = cost = fees = 0.0
    for x in selected:
        if x["state"] not in TERMINAL:
            raise Unverified("ORDER_NOT_TERMINAL")
        if x.get("fee") is None or x.get("avg_price") is None:
            raise Unverified("FILL_ACCOUNTING_MISSING")
        q = float(x["filled"])
        p = float(x["avg_price"])
        f = float(x["fee"])
        if not all(math.isfinite(v) for v in (q, p, f)) or q <= 0 or p <= 0:
            raise Unverified("FILL_ACCOUNTING_INVALID")
        qty += q
        cost += q * p
        fees += f
    return qty, cost / qty if qty else None, fees


def validate_trade(trade):
    positive = (
        trade.base_qty,
        trade.long_contracts,
        trade.short_contracts,
        trade.long_contract_size,
        trade.short_contract_size,
        trade.long_entry,
        trade.short_entry,
        trade.opened_at,
    )
    if not all(
        math.isfinite(float(x)) and float(x) > 0 for x in positive
    ) or not math.isfinite(float(trade.entry_fees)):
        raise Unverified("RUNTIME_ACCOUNTING_INVALID")
    if (
        not all(
            math.isfinite(float(v))
            for v in (trade.recovery_gross, trade.recovery_fees, trade.recovery_capital)
        )
        or trade.recovery_capital < 0
    ):
        raise Unverified("RUNTIME_ACCOUNTING_INVALID")
    for qty, size in (
        (trade.long_contracts, trade.long_contract_size),
        (trade.short_contracts, trade.short_contract_size),
    ):
        if not math.isclose(qty * size, trade.base_qty, rel_tol=0.001):
            raise Unverified("RUNTIME_CONTRACT_MISMATCH")


def validate_sides(intents, lv, sv):
    for x in intents:
        expected = (
            ("sell" if x["venue"] == lv else "buy")
            if x["reduce_only"]
            else ("buy" if x["venue"] == lv else "sell")
        )
        if float(x.get("filled") or 0) > 0 and x["side"] != expected:
            raise Unverified("UNEXPECTED_FILL_DIRECTION")


def rebuild(row, payload, intents, snapshot):
    if not intents or any(x["state"] not in TERMINAL for x in intents):
        raise Unverified("ORDER_EVIDENCE_INCOMPLETE")
    reduced = any(
        bool(x["reduce_only"])
        and x["intent_id"].startswith(row["trade_id"] + ":entry-recovery:")
        for x in intents
    )
    if any(
        bool(x["reduce_only"])
        and not x["intent_id"].startswith(row["trade_id"] + ":entry-recovery:")
        and float(x.get("filled") or 0) > 0
        for x in intents
    ):
        raise Unverified("ENTRY_WITH_CLOSE_FILLS_REQUIRES_ACCOUNTING")
    symbol = row["symbol"]
    lv = row["long_venue"]
    sv = row["short_venue"]
    if lv == sv:
        raise Unverified("VENUE_IDENTITY_INVALID")
    validate_sides(intents, lv, sv)
    lc, lp, lf = leg_fills(intents, lv, "buy", False, symbol)
    sc, sp, sf = leg_fills(intents, sv, "sell", False, symbol)
    b = None
    if reduced:
        from .reduced_fill_accounting import basis

        b = basis(row, payload, intents)
        lc, sc, lp, sp, lf, sf = (
            b["remaining_long"],
            b["remaining_short"],
            b["long_entry"],
            b["short_entry"],
            b["entry_fees"],
            0,
        )
    ls = payload.get("long_contract_size")
    ss = payload.get("short_contract_size")
    if ls is None or ss is None:
        raise Unverified("CONTRACT_SIZES_MISSING")
    ls = float(ls)
    ss = float(ss)
    if not all(math.isfinite(x) and x > 0 for x in (ls, ss, lc, sc)):
        raise Unverified("HEDGE_FILL_MISSING")
    lb = lc * ls
    sb = sc * ss
    if abs(lb - sb) > max(lb, sb) * 0.001:
        raise Unverified("HEDGE_FILL_MISMATCH")
    opened = float(payload.get("opened_at") or row["updated_at"])
    if not math.isfinite(opened) or opened <= 0:
        raise Unverified("ENTRY_TIMESTAMP_MISSING")
    trade = RuntimeTrade(
        row["trade_id"],
        symbol,
        lv,
        sv,
        min(lb, sb),
        lc,
        sc,
        ls,
        ss,
        lp,
        sp,
        opened,
        entry_fees=lf + sf,
        recovery_gross=b["recovery_gross"] if b else 0,
        recovery_fees=b["recovery_fees"] if b else 0,
        recovery_capital=b["capital"] if b else 0,
    )
    if not verify_trade(trade, snapshot).safe:
        raise Unverified("PRIVATE_POSITION_MISMATCH")
    return trade


def exit_accounting(trade, intents):
    from .trade_result import finalize

    validate_trade(trade)
    if any(
        bool(x["reduce_only"])
        and x["intent_id"].startswith(trade.trade_id + ":entry-recovery:")
        for x in intents
    ):
        from .reduced_fill_accounting import basis, check_runtime, cycle

        row = dict(
            trade_id=trade.trade_id,
            symbol=trade.symbol,
            long_venue=trade.long_venue,
            short_venue=trade.short_venue,
        )
        payload = dict(
            long_contract_size=trade.long_contract_size,
            short_contract_size=trade.short_contract_size,
            opened_at=trade.opened_at,
        )
        check_runtime(trade, basis(row, payload, intents))
        return cycle(row, payload, intents)[1]
    if trade.recovery_gross or trade.recovery_fees or trade.recovery_capital:
        raise Unverified("REDUCED_EVIDENCE_MISSING")
    validate_sides(intents, trade.long_venue, trade.short_venue)
    if any(x["state"] not in TERMINAL for x in intents):
        raise Unverified("ORDER_EVIDENCE_INCOMPLETE")
    lc, lp, lf = leg_fills(intents, trade.long_venue, "sell", True, trade.symbol)
    sc, sp, sf = leg_fills(intents, trade.short_venue, "buy", True, trade.symbol)
    if not math.isclose(
        lc, trade.long_contracts, rel_tol=1e-10, abs_tol=0
    ) or not math.isclose(sc, trade.short_contracts, rel_tol=1e-10, abs_tol=0):
        raise Unverified("EXIT_FILL_QUANTITY_MISMATCH")
    if lp is None or sp is None:
        raise Unverified("EXIT_PRICE_MISSING")
    capital = trade.base_qty * (trade.long_entry + trade.short_entry) / 2
    return lambda funding: finalize(
        trade.trade_id,
        trade.base_qty,
        trade.long_entry,
        trade.short_entry,
        lp,
        sp,
        trade.entry_fees,
        lf + sf,
        funding,
        capital,
        "RESTART_PRIVATE_VERIFIED_CLOSE",
    )
