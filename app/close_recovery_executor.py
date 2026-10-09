import asyncio
from dataclasses import dataclass, replace
from .close_recovery import plan
from .exchange_executor import SubmitRequest
from .fill_reconcile import reconcile
from .fill_merge import merge_result
from .order_settlement import settle


@dataclass(frozen=True)
class RecoveryOutcome:
    execution: object
    recovered: bool
    reason: str
    recovery_result: object = None


async def recover_close(
    trade, exit_result, long_executor, short_executor, timeout=8, tolerance=1e-12
):
    p = plan(trade, exit_result, tolerance)
    if not p.required:
        return RecoveryOutcome(exit_result, True, "FLAT_BY_FILLS")
    if not p.venue:
        return RecoveryOutcome(exit_result, False, p.reason)
    executor = long_executor if p.venue == trade.long_venue else short_executor
    req = SubmitRequest(trade.symbol, p.side, p.contracts, "market", None, True, False)
    try:
        if hasattr(executor, "submit_intent"):
            from .recovery_intent_factory import close as recovery_intent

            intent = recovery_intent(
                trade.trade_id, p.venue, trade.symbol, p.side, p.contracts
            )
            req = SubmitRequest(
                trade.symbol,
                p.side,
                p.contracts,
                "market",
                None,
                True,
                False,
                intent.intent_id,
            )
            r, status = await asyncio.wait_for(
                executor.submit_intent(intent, req), timeout
            )
            if r is None:
                return RecoveryOutcome(exit_result, False, status)
        else:
            r = await asyncio.wait_for(executor.submit(req), timeout)
    except Exception as e:
        return RecoveryOutcome(exit_result, False, "RECOVERY_" + type(e).__name__)
    r, evidence = await settle(executor, r, trade.symbol, p.contracts, timeout)
    if r is None:
        return RecoveryOutcome(exit_result, False, "RECOVERY_" + evidence)
    if p.venue == trade.long_venue:
        lr = merge_result(exit_result.long_result, r)
        sr = exit_result.short_result
    else:
        lr = exit_result.long_result
        sr = merge_result(exit_result.short_result, r)
    rec = reconcile(
        lr.filled, trade.long_contract_size, sr.filled, trade.short_contract_size
    )
    flat = (
        lr.filled >= trade.long_contracts - tolerance
        and sr.filled >= trade.short_contracts - tolerance
    )
    merged = replace(
        exit_result,
        long_result=lr,
        short_result=sr,
        flat=flat,
        mismatch_pct=rec.mismatch_pct,
    )
    return RecoveryOutcome(merged, flat, "RECOVERED" if flat else "RECOVERY_PARTIAL", r)
