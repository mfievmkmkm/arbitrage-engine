"""Strict quote proof for an executable IOC entry or recovery market order."""

import math
import time
from .recovery_market import validate_evidence as recovery, levels, executable
from .native_order_plan import number


def validate(request, venue, now=None):
    e = request.market_evidence
    if not isinstance(e, dict) or e.get("source") != "PUBLIC_IOC_ENTRY_V1":
        return recovery(request, venue, now)
    if (
        request.order_type != "limit"
        or request.ioc is not True
        or request.reduce_only is not False
    ):
        raise ValueError("ENTRY_EVIDENCE_TYPE_INVALID")
    if tuple(e.get(k) for k in ("venue", "symbol", "side", "contracts")) != (
        venue,
        request.symbol,
        request.side,
        request.qty,
    ):
        raise ValueError("ENTRY_EVIDENCE_SCOPE_MISMATCH")
    now = time.time() if now is None else now
    ts, started, received = [
        float(number(e.get(k), k)) for k in ("book_ts", "started_at", "received_at")
    ]
    if (
        not math.isfinite(now)
        or not ts <= received <= now
        or not started <= received
        or now - min(ts, started) > 1.5
    ):
        raise ValueError("ENTRY_EVIDENCE_STALE")
    bids, asks = levels(e.get("bids"), "bids"), levels(e.get("asks"), "asks")
    if bids[0][0] >= asks[0][0]:
        raise ValueError("ENTRY_EVIDENCE_CROSSED")
    rows = asks if request.side == "buy" else bids if request.side == "sell" else None
    if rows is None:
        raise ValueError("ENTRY_EVIDENCE_SIDE_INVALID")
    qty = float(number(request.qty, "ENTRY_CONTRACTS"))
    size = float(number(e.get("contract_size"), "CONTRACT_SIZE"))
    average, worst = executable(rows, qty)
    price = float(number(request.price, "ENTRY_LIMIT"))
    if not math.isclose(
        float(number(e.get("base_qty"), "BASE_QTY")),
        qty * size,
        rel_tol=1e-12,
        abs_tol=0,
    ):
        raise ValueError("ENTRY_EVIDENCE_UNITS_MISMATCH")
    if (
        (price < worst or price > rows[0][0] * 1.002)
        if request.side == "buy"
        else (price > worst or price < rows[0][0] * 0.998)
    ):
        raise ValueError("ENTRY_LIMIT_NOT_EXECUTABLE_OR_SLIPPAGE_HIGH")
    return e
