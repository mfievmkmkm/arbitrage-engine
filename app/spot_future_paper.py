import time
from dataclasses import dataclass


@dataclass
class Position:
    id: int
    base: str
    exchange: str
    direction: str
    notional: float
    base_qty: float
    spot_entry: float
    future_entry: float
    opened_at: float
    best_net: float = 0
    net: float = 0
    status: str = "OPEN"
    entry_fee_pct: float | None = None
    safety_pct: float | None = None
    last_mark: dict | None = None


def open_from(op, pid=0):
    p = op["prices"]
    direction = op["direction"]
    spot = p["spot_buy"] if direction == "LONG_SPOT_SHORT_FUTURE" else p["spot_sell"]
    future = (
        p["future_sell"] if direction == "LONG_SPOT_SHORT_FUTURE" else p["future_buy"]
    )
    position = Position(
        pid,
        op["base"],
        op["exchange"],
        direction,
        op["notional"],
        op["base_qty"],
        spot,
        future,
        op.get("ts", time.time()),
        entry_fee_pct=float(op["fee_pct"]),
        safety_pct=float(op["safety_pct"]),
    )
    mark(position, op)
    return position


def mark(pos, op):
    p = op["prices"]
    q = pos.base_qty
    gross = (
        ((p["spot_sell"] - pos.spot_entry) + (pos.future_entry - p["future_buy"])) * q
        if pos.direction == "LONG_SPOT_SHORT_FUTURE"
        else ((pos.spot_entry - p["spot_buy"]) + (p["future_sell"] - pos.future_entry))
        * q
    )
    if not abs(float(op["base_qty"]) - q) <= max(q, 1e-12) * 1e-8:
        raise ValueError("EXIT_QUOTE_QUANTITY_MISMATCH")
    if pos.entry_fee_pct is None:
        pos.entry_fee_pct = float(op["fee_pct"])
    if pos.safety_pct is None:
        pos.safety_pct = float(op["safety_pct"])
    spot_exit = (
        p["spot_sell"] if pos.direction == "LONG_SPOT_SHORT_FUTURE" else p["spot_buy"]
    )
    future_exit = (
        p["future_buy"]
        if pos.direction == "LONG_SPOT_SHORT_FUTURE"
        else p["future_sell"]
    )
    entry_fees = q * (pos.spot_entry + pos.future_entry) * pos.entry_fee_pct / 400
    exit_fees = q * (spot_exit + future_exit) * float(op["fee_pct"]) / 400
    safety = pos.notional * pos.safety_pct / 100
    drag = entry_fees + exit_fees + safety
    fund = pos.notional * op.get("settled_funding_pct", 0) / 100
    pos.net = gross - drag + fund
    pos.best_net = max(pos.best_net, pos.net)
    pos.last_mark = {
        "ts": op.get("ts", time.time()),
        "base_qty": q,
        "gross": gross,
        "entry_fees": entry_fees,
        "exit_fees": exit_fees,
        "safety": safety,
        "funding": fund,
        "net": pos.net,
        "spot_exit": spot_exit,
        "future_exit": future_exit,
        "mode": "PAPER_MODEL",
    }
    return pos.net
