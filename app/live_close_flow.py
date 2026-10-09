import asyncio
from dataclasses import dataclass
from .close_flow import close_trade
from .live_close_gate import confirm
from .close_recovery_executor import recover_close
from .trade_result import finalize
from .private_close_recovery import recover_from_private


@dataclass(frozen=True)
class VerifiedClose:
    result: object
    execution: object
    status: str


async def _snapshot(source):
    value = source() if callable(source) else source
    if hasattr(value, "__await__"):
        value = await value
    return value


def _finalize_recovered(
    t, x, long_exit_price, short_exit_price, exit_fees, funding, reason
):
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
    fees = x.long_result.fee + x.short_result.fee if exit_fees == 0 else exit_fees
    f = t.funding if funding is None else funding
    capital = t.base_qty * ((t.long_entry + t.short_entry) / 2)
    return finalize(
        t.trade_id,
        t.base_qty,
        t.long_entry,
        t.short_entry,
        long_px,
        short_px,
        t.entry_fees,
        fees,
        f,
        capital,
        reason,
    )


async def close_verified(
    t,
    long_executor,
    short_executor,
    private_snapshot,
    long_exit_price,
    short_exit_price,
    private_attempts=3,
    private_delay=0.1,
    recovery_timeout=8,
    **kwargs
):
    result, x, status = await close_trade(
        t, long_executor, short_executor, long_exit_price, short_exit_price, **kwargs
    )
    if status == "EXIT_PARTIAL_REQUIRES_RECOVERY":
        recovery = await recover_close(
            t, x, long_executor, short_executor, recovery_timeout
        )
        x = recovery.execution
        if not recovery.recovered:
            if recovery.reason != "BOTH_LEGS_RESIDUAL_RECONCILE":
                return VerifiedClose(
                    None, x, "CLOSE_RECOVERY_FAILED_" + recovery.reason
                )
            snapshot = await _snapshot(private_snapshot)
            pr = await recover_from_private(
                t, snapshot, long_executor, short_executor, recovery_timeout
            )
            from dataclasses import replace
            from .fill_merge import merge_result
            from .fill_reconcile import reconcile

            lr = (
                merge_result(x.long_result, pr.long_result)
                if pr.long_result is not None
                else x.long_result
            )
            sr = (
                merge_result(x.short_result, pr.short_result)
                if pr.short_result is not None
                else x.short_result
            )
            rec = reconcile(
                lr.filled, t.long_contract_size, sr.filled, t.short_contract_size
            )
            x = replace(
                x,
                long_result=lr,
                short_result=sr,
                mismatch_pct=rec.mismatch_pct,
                flat=lr.filled >= t.long_contracts and sr.filled >= t.short_contracts,
            )
            if not pr.recovered:
                return VerifiedClose(None, x, "CLOSE_RECOVERY_FAILED_" + pr.reason)
            last_reason = "PRIVATE_STATE_UNTRUSTED"
            for attempt in range(max(1, private_attempts)):
                fresh = await _snapshot(private_snapshot)
                from .private_residual import verify

                residual = verify(fresh, t.symbol, t.long_venue, t.short_venue)
                if residual.flat:
                    return VerifiedClose(None, x, "CLOSED_RECOVERED_PRIVATE")
                last_reason = residual.reason
                if attempt + 1 < private_attempts and private_delay > 0:
                    await asyncio.sleep(private_delay)
            return VerifiedClose(None, x, "CLOSE_UNVERIFIED_" + last_reason)
        result = _finalize_recovered(
            t,
            x,
            long_exit_price,
            short_exit_price,
            kwargs.get("exit_fees", 0),
            kwargs.get("funding"),
            kwargs.get("reason", "EXIT"),
        )
        status = "CLOSED"
    if status != "CLOSED":
        return VerifiedClose(result, x, status)
    last_reason = "PRIVATE_STATE_UNTRUSTED"
    for attempt in range(max(1, private_attempts)):
        snapshot = await _snapshot(private_snapshot)
        c = confirm(x, snapshot, t.symbol, t.long_venue, t.short_venue)
        if c.closed:
            return VerifiedClose(result, x, "CLOSED")
        last_reason = c.reason
        if attempt + 1 < private_attempts and private_delay > 0:
            await asyncio.sleep(private_delay)
    return VerifiedClose(result, x, "CLOSE_UNVERIFIED_" + last_reason)
