from dataclasses import dataclass
from .persisted_exit_runner import run as exit_run
from .close_recovery_executor import recover_close
from .private_residual import verify as verify_flat
from .trade_result import finalize
import asyncio


@dataclass(frozen=True)
class LifecycleClose:
    closed: bool
    status: str
    result: object = None
    execution: object = None


async def close(
    trade,
    long_executor,
    short_executor,
    private_snapshot,
    reason="EXIT",
    funding=None,
    attempts=3,
    delay=0.1,
    recovery_market_reader=None,
):
    x = await exit_run(
        trade, long_executor, short_executor, market_reader=recovery_market_reader
    )
    if x.execution is None:
        return LifecycleClose(False, x.status)
    if x.status == "EXIT_ACTUAL_SLIPPAGE_STOP":
        return LifecycleClose(False, x.status, None, x.execution)
    execution = x.execution
    if not execution.flat:
        r = await recover_close(
            trade,
            execution,
            long_executor,
            short_executor,
            market_reader=recovery_market_reader,
        )
        execution = r.execution
        if not r.recovered:
            return LifecycleClose(False, "RECOVERY_FAILED:" + r.reason, None, execution)
    last = "PRIVATE_STATE_UNTRUSTED"
    for i in range(max(1, attempts)):
        try:
            s = private_snapshot()
            s = await s if hasattr(s, "__await__") else s
        except Exception:
            s = None
        if s:
            v = verify_flat(s, trade.symbol, trade.long_venue, trade.short_venue)
            if v.flat:
                if (
                    execution.long_result.avg_price is None
                    or execution.short_result.avg_price is None
                ):
                    return LifecycleClose(
                        False, "ACCOUNTING_PRICE_MISSING", None, execution
                    )
                fees = execution.long_result.fee + execution.short_result.fee
                f = trade.funding if funding is None else funding
                capital = trade.recovery_capital or trade.base_qty * (
                    (trade.long_entry + trade.short_entry) / 2
                )
                result = finalize(
                    trade.trade_id,
                    trade.base_qty,
                    trade.long_entry,
                    trade.short_entry,
                    execution.long_result.avg_price,
                    execution.short_result.avg_price,
                    trade.entry_fees,
                    fees,
                    f,
                    capital,
                    reason,
                    recovery_gross=trade.recovery_gross,
                    recovery_fees=trade.recovery_fees,
                )
                return LifecycleClose(True, "CLOSED_VERIFIED", result, execution)
            last = v.reason
        if i + 1 < attempts:
            await asyncio.sleep(delay)
    return LifecycleClose(False, "CLOSE_UNVERIFIED:" + last, None, execution)
