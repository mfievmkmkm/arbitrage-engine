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
    hybrid_requote=None,
    recovery_market_reader=None,
    recovery_assessor=None,
    prepared_requests=None,
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
        if hasattr(durable_store, "reserve_entry"):
            if not await durable_store.reserve_entry(trade_id, **meta):
                return LiveEntryResult(
                    False, "DURABLE_CAPACITY_RESERVED", trade_id, admission=adm
                )
        else:
            await durable_store.phase(trade_id, "PLANNED", **meta)
        await durable_store.phase(trade_id, "ENTRY_SUBMITTING")

    async def leg(name, leg, ex, price, order_policy=None):
        order_policy = order_policy or policy
        req = SubmitRequest(
            symbol,
            leg.side,
            leg.contracts,
            order_policy.order_type,
            None if order_policy.order_type == "market" else price,
            False,
            order_policy.ioc,
            trade_id + ":" + name,
            price if order_policy.order_type == "market" else None,
        )
        if prepared_requests is not None and name in ("entry-long", "entry-short"):
            from dataclasses import replace

            template = prepared_requests[leg.venue]
            if (
                template.symbol,
                template.side,
                template.qty,
                template.order_type,
                template.reduce_only,
                template.ioc,
                template.price,
            ) != (
                req.symbol,
                req.side,
                req.qty,
                req.order_type,
                req.reduce_only,
                req.ioc,
                req.price,
            ):
                return None, "ENTRY_PREPARED_REQUEST_MISMATCH"
            req = replace(template, client_order_id=req.client_order_id)
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
    fallback_used = False
    if lr.filled == 0 and sr.filled == 0:
        if hybrid_requote is None:
            return LiveEntryResult(False, "ENTRY_NO_FILL", trade_id, admission=adm)
        from .hybrid_entry import assess, policy as fallback_policy

        try:
            proof = await asyncio.wait_for(hybrid_requote(), timeout)
            allowed, reason = assess(lr, sr, plan, proof, long_price, short_price)
        except Exception:
            allowed, reason = False, "FALLBACK_REQUOTE_UNAVAILABLE"
        if not allowed:
            return LiveEntryResult(
                False, "FALLBACK_BLOCKED:" + reason, trade_id, admission=adm
            )
        fp = fallback_policy()
        fallback_used = True
        adm = prepare(
            plan,
            fp,
            proof.long_price,
            proof.short_price,
            fee_schedule,
            min_net_edge_usd,
            **admission_kwargs
        )
        if not adm.allowed:
            return LiveEntryResult(
                False,
                "FALLBACK_NET_OR_RISK_BLOCKED:" + adm.reason,
                trade_id,
                admission=adm,
            )
        if durable_store is not None:
            await durable_store.phase(
                trade_id,
                "ENTRY_SUBMITTING",
                entry_stage="MARKET_FALLBACK",
                fallback_book_ts=proof.book_ts,
                fallback_native_plan=proof.native.row(),
                fallback_long_quote=proof.long_price,
                fallback_short_quote=proof.short_price,
            )
        l, s = await asyncio.gather(
            leg("entry-market-long", plan.long, long_executor, proof.long_price, fp),
            leg(
                "entry-market-short", plan.short, short_executor, proof.short_price, fp
            ),
        )
        lr, ls = l
        sr, ss = s
        if lr is None or sr is None:
            return LiveEntryResult(
                False, "FALLBACK_UNCERTAIN:" + ls + ":" + ss, trade_id, admission=adm
            )
        (lr, lc), (sr, sc) = await asyncio.gather(
            settle(long_executor, lr, symbol, plan.long.contracts, timeout),
            settle(short_executor, sr, symbol, plan.short.contracts, timeout),
        )
        if lr is None or sr is None:
            return LiveEntryResult(
                False,
                "FALLBACK_WORKING_ORDER_UNCERTAIN:" + lc + ":" + sc,
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
        if recovery_assessor is not None:
            try:
                assessment = await asyncio.wait_for(
                    recovery_assessor(plan, lr, sr), timeout
                )
                if durable_store is not None:
                    await durable_store.phase(
                        trade_id,
                        "ENTRY_SUBMITTING",
                        recovery_assessment=assessment.row(),
                    )
                if assessment.action == "BLOCKED":
                    if durable_store is not None:
                        await durable_store.phase(
                            trade_id,
                            "UNKNOWN",
                            entry_hold_reason="RECOVERY_ASSESSMENT_BLOCKED:"
                            + assessment.reason,
                        )
                    return LiveEntryResult(
                        False,
                        "RECOVERY_ASSESSMENT_BLOCKED:" + assessment.reason,
                        trade_id,
                        initial,
                        admission=adm,
                    )
            except Exception:
                if durable_store is not None:
                    await durable_store.phase(
                        trade_id,
                        "UNKNOWN",
                        entry_hold_reason="RECOVERY_ASSESSMENT_UNKNOWN",
                    )
                return LiveEntryResult(
                    False,
                    "RECOVERY_ASSESSMENT_UNKNOWN",
                    trade_id,
                    initial,
                    admission=adm,
                )
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
            0 if recovery_market_reader is not None else edge_pct,
            0,
            max(edge_pct, 0) + 1e-12,
            long_round or (lambda x: x),
            timeout,
            trade_id=trade_id,
            short_round=short_round,
            market_reader=recovery_market_reader,
        )
        from .recovery_assessment import reduction_effects

        try:
            effects = reduction_effects(plan, initial, recovery)
        except (ValueError, TypeError):
            if durable_store is not None:
                await durable_store.phase(
                    trade_id,
                    "UNKNOWN",
                    entry_hold_reason="RECOVERY_REDUCTION_ACCOUNTING_UNKNOWN",
                )
            return LiveEntryResult(
                False,
                "RECOVERY_REDUCTION_ACCOUNTING_UNKNOWN",
                trade_id,
                initial,
                None,
                recovery,
                adm,
            )
        if effects is not None and durable_store is not None:
            from dataclasses import asdict

            await durable_store.phase(
                trade_id,
                "UNKNOWN",
                recovery_effects=effects,
                recovery_result=asdict(recovery.result),
                entry_hold_reason="ENTRY_REDUCTION_REQUIRES_PRIVATE_ACCOUNTING",
            )
        if not recovery.completed:
            if durable_store is not None:
                await durable_store.phase(
                    trade_id,
                    "UNKNOWN",
                    entry_hold_reason="ENTRY_RECOVERY_FAILED:" + recovery.error,
                )
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
    if fallback_used and (
        a.long_price > proof.long_price * (1 + 0.002)
        or a.short_price < proof.short_price * (1 - 0.002)
    ):
        if durable_store is not None:
            await durable_store.phase(
                trade_id, "UNKNOWN", entry_hold_reason="FALLBACK_ACTUAL_SLIPPAGE_STOP"
            )
        return LiveEntryResult(
            False,
            "FALLBACK_ACTUAL_SLIPPAGE_STOP",
            trade_id,
            effective,
            a,
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
    if prepared_requests is not None and (
        a.long_price > prepared_requests[plan.long.venue].price * (1 + 1e-10)
        or a.short_price < prepared_requests[plan.short.venue].price * (1 - 1e-10)
    ):
        from dataclasses import replace

        pv = replace(pv, verified=False, reason="IOC_ACTUAL_LIMIT_VIOLATION")
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
        px = await protective_exit(
            tmp,
            long_executor,
            short_executor,
            timeout,
            market_reader=recovery_market_reader,
        )
        if px.status == "EXIT_ACTUAL_SLIPPAGE_STOP" and durable_store is not None:
            await durable_store.phase(
                trade_id,
                "UNKNOWN",
                entry_hold_reason="PROTECTIVE_EXIT_ACTUAL_SLIPPAGE_STOP",
            )
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
