from dataclasses import dataclass


@dataclass(frozen=True)
class LivePnl:
    long_gross: float
    short_gross: float
    gross: float
    fees: float
    funding: float
    safety_buffer: float
    net: float
    recovery_gross: float = 0


def calculate(
    base_qty,
    long_entry,
    short_entry,
    long_exit,
    short_exit,
    entry_fees=0,
    exit_fees=0,
    funding=0,
    safety_buffer=0,
    recovery_gross=0,
    recovery_fees=0,
):
    lg = (long_exit - long_entry) * base_qty
    sg = (short_entry - short_exit) * base_qty
    gross = lg + sg + recovery_gross
    fees = entry_fees + exit_fees + recovery_fees
    buffer = max(0, float(safety_buffer))
    return LivePnl(
        lg,
        sg,
        gross,
        fees,
        funding,
        buffer,
        gross - fees - buffer + funding,
        recovery_gross,
    )
