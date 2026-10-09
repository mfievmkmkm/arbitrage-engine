"""Write-side exit orchestration. Monitor alone owns final accounting.

Requires explicit runtime authority, a fresh reconciled monitor view and an
atomic durable reservation. It never opens a leg or announces private-flat.
"""

import asyncio
import math
import time
from dataclasses import dataclass
from .runtime_state import RuntimeTrade
from .live_recovery_evidence import validate_trade
from .persisted_exit_runner import run
from .close_recovery_executor import recover_close

SIGNALS = {"TARGET_CAPTURE", "NET_TRAILING", "TIME_STOP", "NET_STOP"}


@dataclass(frozen=True)
class Dispatch:
    trade_id: str
    status: str
    attempted: bool = False


class Coordinator:
    def __init__(
        self,
        durable,
        executors,
        market_reader,
        authority,
        stop,
        clock=time.time,
        max_age=1.5,
        runner=run,
        recovery=recover_close,
        residual_recovery=False,
    ):
        self.durable, self.executors, self.market = durable, executors, market_reader
        self.authority, self.stop, self.clock, self.max_age = (
            authority,
            stop,
            clock,
            max_age,
        )
        self.residual_recovery = residual_recovery
        self.runner, self.recovery = runner, recovery
        self.lock = asyncio.Lock()
        self.latest = []

    def _gate(self, trade, summary, mark):
        if self.market is None or not self.authority(trade):
            return "EXIT_AUTHORITY_REQUIRED"
        if (
            not summary.get("reconciled")
            or not summary.get("private_verified")
            or summary.get("unknown_orders")
        ):
            return "EXIT_RECONCILIATION_REQUIRED"
        if not mark.get("private_verified"):
            return "EXIT_PRIVATE_POSITION_REQUIRED"
        if (mark.get("symbol"), mark.get("long_venue"), mark.get("short_venue")) != (
            trade.symbol,
            trade.long_venue,
            trade.short_venue,
        ):
            return "EXIT_SCOPE_MISMATCH"
        if mark.get("exit_signal") not in SIGNALS:
            return "EXIT_NO_SIGNAL"
        if mark["exit_signal"] in ("TARGET_CAPTURE", "NET_TRAILING") and not mark.get(
            "costs_verified"
        ):
            return "EXIT_COSTS_UNVERIFIED"
        now = self.clock()
        try:
            stamps = (summary["ts"], mark["market_ts"])
            if any(
                isinstance(t, bool)
                or not math.isfinite(t)
                or not 0 <= now - t <= self.max_age
                for t in stamps
            ):
                return "EXIT_VIEW_STALE"
            validate_trade(trade)
        except (KeyError, TypeError, ValueError):
            return "EXIT_VIEW_INVALID"
        if any(v not in self.executors for v in (trade.long_venue, trade.short_venue)):
            return "EXIT_EXECUTOR_MISSING"
        return "OK"

    async def process(self, summary):
        async with self.lock:
            outcomes = []
            rows = {t["trade_id"]: t for t in summary.get("runtime_trades", [])}
            seen = set()
            for mark in summary.get("trades", []):
                if mark.get("strategy") == "spot_futures":
                    continue
                tid = mark.get("trade_id")
                if tid in seen or mark.get("exit_signal") not in SIGNALS:
                    continue
                seen.add(tid)
                try:
                    trade = RuntimeTrade(**rows[tid])
                except (KeyError, TypeError):
                    outcomes.append(Dispatch(tid, "EXIT_RUNTIME_MISSING"))
                    continue
                reason = self._gate(trade, summary, mark)
                if reason != "OK":
                    outcomes.append(Dispatch(tid, reason))
                    continue
                if not await self.durable.claim_exit(trade, mark["exit_signal"]):
                    outcomes.append(Dispatch(tid, "EXIT_ALREADY_CLAIMED_OR_CHANGED"))
                    continue
                # Losing authority during DB IO leaves a durable reservation;
                # it is deliberately not reset to OPEN for an unsafe retry.
                reason = self._gate(trade, summary, mark)
                if reason != "OK":
                    await self.durable.phase(
                        tid, "EXIT_SUBMITTING", exit_hold_reason=reason
                    )
                    self.stop.stop(reason)
                    outcomes.append(Dispatch(tid, reason))
                    continue
                try:
                    long, short = (
                        self.executors[trade.long_venue],
                        self.executors[trade.short_venue],
                    )
                    result = await self.runner(
                        trade, long, short, market_reader=self.market
                    )
                    status = result.status
                    if (
                        result.execution is not None
                        and not result.execution.flat
                        and "SLIPPAGE_STOP" not in status
                    ):
                        recovered = await self.recovery(
                            trade,
                            result.execution,
                            long,
                            short,
                            market_reader=self.market,
                        )
                        status = (
                            "EXIT_FILLS_PENDING_PRIVATE"
                            if recovered.recovered
                            else "EXIT_RECOVERY_REQUIRED:" + recovered.reason
                        )
                    elif (
                        result.execution is not None
                        and result.execution.flat
                        and "SLIPPAGE_STOP" not in status
                    ):
                        status = "EXIT_FILLS_PENDING_PRIVATE"
                    if self.residual_recovery and status in (
                        "EXIT_RECOVERY_REQUIRED:BOTH_LEGS_RESIDUAL_RECONCILE",
                        "EXIT_RECOVERY_REQUIRED:RECOVERY_PARTIAL",
                    ):
                        status = "EXIT_RESIDUAL_PENDING_RECONCILIATION"
                    hold = status not in (
                        "EXIT_FILLS_PENDING_PRIVATE",
                        "EXIT_RESIDUAL_PENDING_RECONCILIATION",
                    )
                    await self.durable.phase(
                        tid,
                        "EXIT_SUBMITTING",
                        exit_dispatch_status=status,
                        exit_dispatch_completed_at=self.clock(),
                        **({"exit_hold_reason": status} if hold else {})
                    )
                    if hold:
                        self.stop.stop(status)
                    outcomes.append(Dispatch(tid, status, True))
                except asyncio.CancelledError:
                    await asyncio.shield(
                        self.durable.phase(
                            tid,
                            "EXIT_SUBMITTING",
                            exit_hold_reason="EXIT_DISPATCH_INTERRUPTED",
                        )
                    )
                    self.stop.stop("EXIT_DISPATCH_INTERRUPTED")
                    raise
                except Exception:
                    await self.durable.phase(
                        tid, "EXIT_SUBMITTING", exit_hold_reason="EXIT_DISPATCH_UNKNOWN"
                    )
                    self.stop.stop("EXIT_DISPATCH_UNKNOWN")
                    outcomes.append(Dispatch(tid, "EXIT_DISPATCH_UNKNOWN", True))
            self.latest = outcomes
            return outcomes
