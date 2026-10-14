from .exit_runner import run as exit_run
from .trade_result import finalize


async def close_trade(
    t,
    long_executor,
    short_executor,
    long_exit_price,
    short_exit_price,
    exit_fees=0,
    funding=None,
    reason="EXIT",
):
    x = await exit_run(
        t.symbol,
        t.long_contracts,
        t.short_contracts,
        t.long_contract_size,
        t.short_contract_size,
        long_executor,
        short_executor,
    )
    if not x.flat:
        return None, x, "EXIT_PARTIAL_REQUIRES_RECOVERY"
    long_px = (
        x.long_result.avg_price
        if x.long_result.avg_price is not None
        else long_exit_price
    )
    short_px = (
        x.short_result.avg_price
        if x.short_result.avg_price is not None
        else short_exit_price
    )
    actual_exit_fees = (
        x.long_result.fee + x.short_result.fee if exit_fees == 0 else exit_fees
    )
    f = t.funding if funding is None else funding
    capital = t.recovery_capital or t.base_qty * ((t.long_entry + t.short_entry) / 2)
    result = finalize(
        t.trade_id,
        t.base_qty,
        t.long_entry,
        t.short_entry,
        long_px,
        short_px,
        t.entry_fees,
        actual_exit_fees,
        f,
        capital,
        reason,
        recovery_gross=t.recovery_gross,
        recovery_fees=t.recovery_fees,
    )
    return result, x, "CLOSED"
