from dataclasses import dataclass
from .live_pnl import calculate


@dataclass(frozen=True)
class TradeResult:
    trade_id: str
    gross: float
    fees: float
    funding: float
    net: float
    roi_pct: float
    reason: str


def finalize(
    trade_id,
    base_qty,
    long_entry,
    short_entry,
    long_exit,
    short_exit,
    entry_fees,
    exit_fees,
    funding,
    capital,
    reason,
    recovery_gross=0,
    recovery_fees=0,
):
    p = calculate(
        base_qty,
        long_entry,
        short_entry,
        long_exit,
        short_exit,
        entry_fees,
        exit_fees,
        funding,
        recovery_gross=recovery_gross,
        recovery_fees=recovery_fees,
    )
    roi = 0 if capital <= 0 else p.net / capital * 100
    return TradeResult(trade_id, p.gross, p.fees, p.funding, p.net, roi, reason)
