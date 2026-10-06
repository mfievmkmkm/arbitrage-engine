import asyncio, uuid, time
from dataclasses import dataclass
from .exchange_executor import SubmitRequest
from .order_policy import choose
from .live_order_intent import OrderIntent
from .fill_reconcile import reconcile
from .effective_entry import merge as merge_entry
from .recovery_flow import recover
from .two_leg_runner import TwoLegResult
from .actual_entry import build as actual_entry
from .live_entry_admission import prepare
from .private_entry_verify import verify as verify_private
from .persisted_exit_runner import run as protective_exit
from .runtime_state import RuntimeTrade
from .order_settlement import settle


@dataclass(frozen=True)
class LiveEntryResult:
    opened: bool
    reason: str
    trade_id: str
    entry: object = None
    actual: object = None
    recovery: object = None
    admission: object = None


async def execute(
    symbol,
    plan,
    long_executor,
    short_executor,
    long_price,
    short_price,
    edge_pct,
    book_spread_pct,
    fee_schedule,
    min_net_edge_usd,
    admission_kwargs,
    timeout=8,
    long_round=None,
    short_round=None,
    private_snapshot=None,
    private_attempts=3,
    private_delay=0.25,
    trade_id_hint=None,
    durable_store=None,
):
    if private_snapshot is None:
        return LiveEntryResult(False, "PRIVATE_STATE_REQUIRED", "")
    policy = choose(edge_pct, book_spread_pct, True)
    adm = prepare(
        plan,
        policy,
        long_price,
        short_price,
        fee_schedule,
        min_net_edge_usd,
        **admission_kwargs
    )
    if not adm.allowed:
        return LiveEntryResult(False, adm.reason, "", admission=adm)
    trade_id = trade_id_hint or uuid.uuid4().hex
    if durable_store is not None:
        if await durable_store.get(trade_id):
            return LiveEntryResult(
                False, "DUPLICATE_DURABLE_TRADE", trade_id, admission=adm
            )
        meta = dict(
            opened_at=time.time(),
            symbol=symbol,
            long_venue=plan.long.venue,
            short_venue=plan.short.venue,
            planned_long=plan.long.contracts,
            planned_short=plan.short.contracts,
            long_contract_size=plan.long.contract_size,
            short_contract_size=plan.short.contract_size,
        )
        await durable_store.phase(trade_id, "PLANNED", **meta)
        await durable_store.phase(trade_id, "ENTRY_SUBMITTING")

    async def leg(name, leg, ex, price):
        req = SubmitRequest(
            symbol,
            leg.side,
            leg.contracts,
            policy.order_type,
            price,
            False,
            policy.ioc,
            trade_id + ":" + name,
        )
        intent = OrderIntent(
            trade_id + ":" + name,
            trade_id,
            leg.venue,
            symbol,
            leg.side,
            leg.contracts,
            False,
        )
        try:
            return await asyncio.wait_for(ex.submit_intent(intent, req), timeout)
        except Exception as e:
            return (None, "SUBMIT_" + type(e).__name__)

    l, s = await asyncio.gather(
        leg("entry-long", plan.long, long_executor, long_price),
        leg("entry-short", plan.short, short_executor, short_price),
    )
    lr, ls = l
    sr, ss = s
    if lr is None or sr is None:
        return LiveEntryResult(
            False, "ENTRY_UNCERTAIN:" + ls + ":" + ss, trade_id, admission=adm
        )
    (lr, lc), (sr, sc) = await asyncio.gather(
        settle(long_executor, lr, symbol, plan.long.contracts, timeout),
        settle(short_executor, sr, symbol, plan.short.contracts, timeout),
    )
    if lr is None or sr is None:
        return LiveEntryResult(
            False,
            "ENTRY_WORKING_ORDER_UNCERTAIN:" + lc + ":" + sc,
            trade_id,
            admission=adm,
        )
    rec = reconcile(
        lr.filled, plan.long.contract_size, sr.filled, plan.short.contract_size
    )
    initial = TwoLegResult(lr, sr, rec.hedged, rec.mismatch_pct)
    recovery = None
    effective = initial
    if not initial.hedged:
        lb = lr.filled * plan.long.contract_size
        sb = sr.filled * plan.short.contract_size
        recovery = await recover(
            symbol,
            plan.long.venue,
            plan.short.venue,
            lb,
            sb,
            plan.long.contract_size,
            plan.short.contract_size,
            long_executor,
            short_executor,
            edge_pct,
            0,
            max(edge_pct, 0) + 1e-12,
            long_round or (lambda x: x),
            timeout,
            trade_id=trade_id,
            short_round=short_round,
        )
        if not recovery.completed:
            return LiveEntryResult(
                False,
                "ENTRY_RECOVERY_FAILED:" + recovery.error,
                trade_id,
                initial,
                None,
                recovery,
                adm,
            )
        merged = merge_entry(plan, initial, recovery)
        if not merged.hedged:
            return LiveEntryResult(
                False,
                "ENTRY_RECOVERY_NOT_HEDGED:" + merged.reason,
                trade_id,
                merged.result,
                None,
                recovery,
                adm,
            )
        effective = merged.result
    try:
        a = actual_entry(effective, plan)
    except RuntimeError as e:
        return LiveEntryResult(
            False,
            "ENTRY_ACTUAL_INVALID:" + str(e),
            trade_id,
            effective,
            None,
            recovery,
            adm,
        )
    if private_snapshot is None:
        return LiveEntryResult(
            False,
            "ENTRY_PRIVATE_UNVERIFIED:PRIVATE_STATE_REQUIRED",
            trade_id,
            effective,
            a,
            recovery,
            adm,
        )
    pv = await verify_private(
        private_snapshot,
        symbol,
        plan.long.venue,
        plan.short.venue,
        a.base_qty,
        private_attempts,
        private_delay,
    )
    if not pv.verified:
        tmp = RuntimeTrade(
            trade_id,
            symbol,
            plan.long.venue,
            plan.short.venue,
            a.base_qty,
            effective.long_result.filled,
            effective.short_result.filled,
            plan.long.contract_size,
            plan.short.contract_size,
            a.long_price,
            a.short_price,
            0,
            entry_fees=a.long_fee + a.short_fee,
        )
        px = await protective_exit(tmp, long_executor, short_executor, timeout)
        from .private_residual import verify as verify_flat

        try:
            flat_snapshot = private_snapshot()
            if hasattr(flat_snapshot, "__await__"):
                flat_snapshot = await flat_snapshot
        except Exception:
            flat_snapshot = None
        flat = (
            flat_snapshot is not None
            and verify_flat(
                flat_snapshot, symbol, plan.long.venue, plan.short.venue
            ).flat
        )
        suffix = "FLATTENED_PRIVATE_VERIFIED" if flat else "FLATTEN_UNCERTAIN"
        return LiveEntryResult(
            False,
            "ENTRY_PRIVATE_UNVERIFIED:" + pv.reason + ":" + suffix,
            trade_id,
            effective,
            a,
            recovery,
            adm,
        )
    return LiveEntryResult(True, "OPENED", trade_id, effective, a, recovery, adm)
