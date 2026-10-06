import uuid
from dataclasses import dataclass
from .live_entry_flow import execute as entry_execute
from .live_entry_commit import commit as commit_entry
from .live_trade_lifecycle import close as close_execute
from .close_acceptance import evaluate as accept_close
from .live_trade_commit import record_open, record_close, remove_verified
from .runtime_state import RuntimeTrade


@dataclass(frozen=True)
class SessionResult:
    ok: bool
    phase: str
    reason: str
    trade: object = None
    result: object = None


async def open_trade(store, journal, existing, entry_args, commit_args, durable=None):
    args = dict(entry_args)
    if durable is not None:
        await durable.init()
        args["trade_id_hint"] = args.get("trade_id_hint") or uuid.uuid4().hex
        args["durable_store"] = durable
    r = await entry_execute(**args)
    if not r.opened:
        if (
            durable is not None
            and r.trade_id
            and r.reason != "DUPLICATE_DURABLE_TRADE"
            and await durable.get(r.trade_id)
        ):
            # Failure does not prove that either venue is flat. Preserve recovery authority.
            await durable.phase(r.trade_id, "UNKNOWN", reason=r.reason)
        return SessionResult(False, "ENTRY_FAILED", r.reason, result=r)
    if durable is not None:
        a = r.actual
        p = commit_args["plan"]
        t = RuntimeTrade(
            r.trade_id,
            commit_args["symbol"],
            commit_args["long_venue"],
            commit_args["short_venue"],
            a.base_qty,
            r.entry.long_result.filled,
            r.entry.short_result.filled,
            p.long.contract_size,
            p.short.contract_size,
            a.long_price,
            a.short_price,
            commit_args["opened_at"],
            entry_fees=a.long_fee + a.short_fee,
        )
        await durable.phase(
            r.trade_id,
            "HEDGED_PRIVATE_VERIFIED",
            runtime_trade=t.row(),
            actual_long=t.long_contracts,
            actual_short=t.short_contracts,
            long_price=t.long_entry,
            short_price=t.short_entry,
            fees=t.entry_fees,
        )
    c = commit_entry(store, existing, r, **commit_args)
    if not c.committed:
        return SessionResult(False, "COMMIT_FAILED", c.reason, result=r)
    await record_open(
        journal,
        r.trade_id,
        commit_args["symbol"],
        commit_args["long_venue"],
        commit_args["short_venue"],
        r.entry,
        r.recovery,
    )
    if durable is not None:
        await durable.phase(r.trade_id, "OPEN")
    return SessionResult(True, "OPEN", r.reason, c.trade)


async def close_trade(
    store,
    journal,
    trades,
    trade,
    long_executor,
    short_executor,
    private_snapshot,
    reason="EXIT",
    durable=None,
):
    if durable is not None:
        row = await durable.get(trade.trade_id)
        # A previous exit may have reached a venue. Never resend without reconciliation.
        if not row or row["phase"] not in ("OPEN", "HEDGED_PRIVATE_VERIFIED"):
            return SessionResult(
                False, "CLOSE_BLOCKED", "DURABLE_EXIT_RECONCILIATION_REQUIRED", trade
            )
        await durable.phase(
            trade.trade_id, "EXIT_SUBMITTING", reason=reason, runtime_trade=trade.row()
        )
    r = await close_execute(
        trade, long_executor, short_executor, private_snapshot, reason
    )
    a = accept_close(r, True, r.closed)
    if not a.accepted:
        return SessionResult(False, "CLOSE_FAILED", a.reason, trade, r)
    if durable is not None:
        await durable.phase(
            trade.trade_id,
            "CLOSED_PRIVATE_VERIFIED",
            result=r.result.__dict__,
            reason=r.status,
        )
    await record_close(journal, trade, r)
    rm = remove_verified(store, trades, trade.trade_id, r)
    if not rm.removed:
        return SessionResult(False, "REMOVE_FAILED", rm.reason, trade, r)
    return SessionResult(True, "CLOSED", r.status, None, r.result)
