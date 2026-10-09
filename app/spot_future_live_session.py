"""Durable sequential forward cash-and-carry lifecycle.

Not registered in main yet: a separate spot-account acceptance and monitor
ownership integration are required. Injected authority never defaults to True.
Every stage is claimed before send; restart observes, never blindly replays.
"""

import asyncio
import json
import time
import math
import aiosqlite
from dataclasses import replace
from .live_order_intent import OrderIntent
from .safe_executor import SafeExecutor
from .spot_executor import SpotExecutor, SpotOrderReader
from .ccxt_executor import CCXTExecutor
from .private_order_reader import Reader as FutureOrderReader
from .private_reader import PrivateReader
from .spot_account_reader import Reader as CashReader
from .spot_future_native_plan import hedge, spot_close
from .spot_future_live_preflight import quote
from .spot_future_cashflow import rebuild, verify_private
from .durable_order_reconcile import reconcile_and_persist
from .order_settlement import settle
from .recovery_market import Reader as RecoveryReader
from .exchange_executor import SubmitRequest


class Session:
    def __init__(
        self,
        durable,
        diary,
        admission,
        entry_authority,
        exit_authority,
        halt=None,
        clock=time.time,
    ):
        self.store, self.diary, self.admission = durable, diary, admission
        self.entry_authority, self.exit_authority = entry_authority, exit_authority
        self.halt, self.clock = halt, clock
        self.lock = asyncio.Lock()

    async def _hold(self, tid, reason):
        await self.store.phase(tid, "CASH_HOLD", cash_hold_reason=reason)
        if self.halt:
            self.halt(reason)
        return dict(status="HOLD", reason=reason, trade_id=tid)

    async def _claim(self, tid, expected, next_phase):
        async with aiosqlite.connect(self.store.path) as d:
            await d.execute("BEGIN IMMEDIATE")
            cur = await d.execute(
                "UPDATE live_trades SET phase=?,updated_at=? WHERE trade_id=? AND phase=?",
                (next_phase, self.clock(), tid, expected),
            )
            claimed = cur.rowcount == 1
            await d.commit()
            return claimed

    def _clients(self, p):
        return (
            self.admission.spot_clients[p.venue],
            self.admission.future_clients[p.venue],
        )

    async def _send(
        self, tid, stage, venue, client, request, spot=False, closing=False
    ):
        gate = self.exit_authority if closing else self.entry_authority
        executor = SafeExecutor(
            venue,
            SpotExecutor(venue, client) if spot else CCXTExecutor(venue, client),
            self.diary,
            lambda: gate(venue.split(":")[0]),
            lambda: self.exit_authority(venue.split(":")[0]),
        )
        iid = tid + ":cash:" + stage
        r = replace(request, client_order_id=iid)
        intent = OrderIntent(iid, tid, venue, r.symbol, r.side, r.qty, r.reduce_only)
        result, state = await executor.submit_intent(intent, r)
        result, settled = await settle(executor, result, r.symbol, r.qty)
        if result is None:
            raise ValueError(state + ":" + settled)
        from .order_status import normalize
        from .recovery_market import actual_slippage

        if not await self.diary.update_order_intent_reconciled(
            iid, normalize(result.status, result.filled, r.qty), result
        ):
            raise ValueError("CASH_SETTLEMENT_JOURNAL_CONFLICT")
        if r.order_type == "market" and actual_slippage(r, result):
            raise ValueError("CASH_ACTUAL_MARKET_SLIPPAGE_LIMIT")
        return result

    async def _flow(self, tid, p):
        return rebuild(
            await self.diary.order_intents(tid),
            tid,
            p.venue + ":spot",
            p.venue,
            p.spot_symbol,
            p.future_symbol,
            p.base,
            p.contract_size,
        )

    async def _private(self, p, flow):
        s, f = self._clients(p)
        spot, positions, orders = await asyncio.wait_for(
            asyncio.gather(
                CashReader(p.venue + ":spot", s, clock=self.clock).snapshot([p.base]),
                PrivateReader(p.venue, f).positions(),
                PrivateReader(p.venue, f).orders(),
            ),
            10,
        )
        if orders:
            raise ValueError("CASH_WORKING_FUTURE_ORDER")
        for pos in positions:
            if pos.venue != p.venue:
                raise ValueError("CASH_PRIVATE_VENUE_MISMATCH")
        return verify_private(
            flow,
            p.baseline_total,
            spot,
            p.base,
            positions,
            p.future_symbol,
            p.contract_size,
            self.clock(),
        )

    async def enter(self, op, trade_id):
        async with self.lock:
            # A persisted stage can have reached the exchange even without an ID.
            if await self.store.get(trade_id):
                return dict(status="RECONCILE_REQUIRED", trade_id=trade_id)
            v = op.get("exchange")
            if not self.entry_authority(v):
                return dict(status="BLOCKED", reason="CASH_ENTRY_AUTHORITY_REQUIRED")
            try:
                p = await self.admission.prepare(op)
                p.validate_time(self.clock())
            except Exception as error:
                return dict(status="BLOCKED", reason=str(error))
            # Admission cannot bypass loss/equity limits already used by FF.
            async with aiosqlite.connect(self.store.path) as d:
                cur = await d.execute(
                    "SELECT COALESCE(SUM(net),0),COALESCE(SUM(CASE WHEN ts>=? THEN net ELSE 0 END),0) FROM live_results",
                    (int(self.clock() // 86400) * 86400,),
                )
                total, daily = await cur.fetchone()
            if (
                not all(math.isfinite(float(x)) for x in (daily, total))
                or daily <= -self.admission.bankroll * 0.02
                or p.budget > min(5, (self.admission.bankroll + total) * 0.1)
            ):
                return dict(status="BLOCKED", reason="CASH_REALIZED_RISK_LIMIT")
            meta = dict(
                strategy="spot_futures",
                symbol=p.future_symbol,
                long_venue=v + ":spot",
                short_venue=v,
                planned_long=p.spot.qty,
                planned_short=p.future.qty * p.contract_size,
                cash_plan=dict(
                    venue=v,
                    base=p.base,
                    spot_symbol=p.spot_symbol,
                    future_symbol=p.future_symbol,
                    contract_size=p.contract_size,
                    baseline_total=p.baseline_total,
                    spot_fee_rate=p.spot_fee_rate,
                    future_fee_rate=p.future_fee_rate,
                    opened_at=self.clock(),
                ),
            )
            if not await self.store.reserve_entry(trade_id, **meta):
                return dict(status="BLOCKED", reason="GLOBAL_LIVE_CAPACITY")
            try:
                if not await self._claim(trade_id, "PLANNED", "CASH_SPOT_SUBMITTING"):
                    return await self._hold(trade_id, "CASH_STAGE_CLAIM_CONFLICT")
                s, f = self._clients(p)
                spot = await self._send(
                    trade_id, "spot-entry", v + ":spot", s, p.spot, spot=True
                )
                flow = await self._flow(trade_id, p)
                await self._private(p, flow)
                if spot.filled == 0:
                    await self.store.phase(
                        trade_id,
                        "ABORTED",
                        cash_reason="TERMINAL_ZERO_FILL_PRIVATE_VERIFIED",
                    )
                    return dict(status="ABORTED", trade_id=trade_id)
                await self.store.phase(
                    trade_id, "CASH_SPOT_TERMINAL", cashflow=flow.row()
                )
                try:
                    r, base = hedge(f, p.future_symbol, flow.spot_base, p.future.price)
                    r = await quote(f, v, r, p.contract_size, clock=self.clock)
                    residual_usd = (flow.spot_base - base) * spot.avg_price
                    entry_cost = -flow.spot_cash + flow.quote_fees
                    # Base fees are already reflected in available credited quantity.
                    forecast = (
                        base * r.price
                        - entry_cost
                        - 2 * base * r.price * p.future_fee_rate
                        - flow.spot_base * spot.avg_price * p.spot_fee_rate
                        - p.loss_allowance
                    )
                    if (
                        residual_usd > 0.05
                        or forecast < p.minimum_net
                        or base * r.price > p.budget
                    ):
                        raise ValueError("CASH_POST_FILL_NET_OR_RESIDUAL_LIMIT")
                except Exception:
                    return await self._close(
                        trade_id,
                        p,
                        "CASH_SPOT_TERMINAL",
                        "ENTRY_HEDGE_PREFLIGHT_FAILED",
                    )
                if not await self._claim(
                    trade_id, "CASH_SPOT_TERMINAL", "CASH_FUTURE_SUBMITTING"
                ):
                    return await self._hold(trade_id, "CASH_STAGE_CLAIM_CONFLICT")
                await self._send(trade_id, "future-entry", v, f, r)
                flow = await self._flow(trade_id, p)
                proof = await self._private(p, flow)
                mismatch_usd = abs(flow.spot_base - flow.future_base) * spot.avg_price
                if flow.future_base == 0 or mismatch_usd > 0.05:
                    await self.store.phase(
                        trade_id, "CASH_ENTRY_PARTIAL", cashflow=flow.row()
                    )
                    return await self._close(
                        trade_id, p, "CASH_ENTRY_PARTIAL", "ENTRY_PARTIAL_HEDGE"
                    )
                await self.store.phase(
                    trade_id, "CASH_OPEN", cashflow=flow.row(), cash_private=proof
                )
                return dict(status="OPEN", trade_id=trade_id, cashflow=flow.row())
            except (Exception, asyncio.CancelledError) as error:
                result = await asyncio.shield(
                    self._hold(trade_id, "CASH_ENTRY_UNKNOWN:" + str(error))
                )
                if isinstance(error, asyncio.CancelledError):
                    raise
                return result

    async def _close(self, tid, p, expected, reason, stage_suffix=""):
        flow = await self._flow(tid, p)
        await self._private(p, flow)
        if not self.exit_authority(p.venue):
            return await self._hold(tid, "CASH_EXIT_AUTHORITY_REQUIRED")
        if not await self._claim(tid, expected, "CASH_EXIT_SUBMITTING"):
            return dict(status="RECONCILE_REQUIRED", trade_id=tid)
        try:
            s, f = self._clients(p)
            # Close derivative first. Spot sale cannot exceed owned credit.
            if flow.future_base > 0:
                r = SubmitRequest(
                    p.future_symbol,
                    "buy",
                    flow.future_base / p.contract_size,
                    "market",
                    reduce_only=True,
                )
                r = await RecoveryReader({p.venue: f}, clock=self.clock).quote(
                    p.venue, r, p.contract_size
                )
                await self._send(
                    tid, "future-exit" + stage_suffix, p.venue, f, r, closing=True
                )
                flow = await self._flow(tid, p)
                await self._private(p, flow)
                if flow.future_base > 0:
                    return await self._hold(tid, "CASH_FUTURE_EXIT_RESIDUAL")
            if flow.spot_base > 0:
                # Reference here is only used to construct native sizing; quote refetches.
                raw = await asyncio.wait_for(
                    s.fetch_order_book(p.spot_symbol, limit=20), 8
                )
                try:
                    r = spot_close(
                        s,
                        p.spot_symbol,
                        flow.spot_base,
                        raw["bids"][0][0],
                        p.spot_fee_rate,
                    )
                except ValueError as error:
                    if (
                        str(error)
                        not in (
                            "NATIVE_COST_MIN",
                            "NATIVE_AMOUNT_MIN",
                            "FORMATTED_AMOUNT_INVALID",
                        )
                        or flow.spot_cost_basis > 0.05
                    ):
                        raise
                else:
                    r = await quote(s, p.venue + ":spot", r, 1, True, self.clock)
                    await self._send(
                        tid,
                        "spot-exit" + stage_suffix,
                        p.venue + ":spot",
                        s,
                        r,
                        spot=True,
                        closing=True,
                    )
            flow = await self._flow(tid, p)
            proof = await self._private(p, flow)
            await self.store.phase(
                tid,
                "CASH_ACCOUNTING_PENDING",
                cashflow=flow.row(),
                cash_private=proof,
                cash_close_reason=reason,
                cash_closed_at=self.clock(),
            )
            return dict(status="ACCOUNTING_PENDING", trade_id=tid, cashflow=flow.row())
        except (Exception, asyncio.CancelledError) as error:
            result = await asyncio.shield(
                self._hold(tid, "CASH_EXIT_UNKNOWN:" + str(error))
            )
            if isinstance(error, asyncio.CancelledError):
                raise
            return result

    async def reconcile(self, trade_id):
        """Read-only restart observation: no submit, cancel, or automatic stage replay."""
        row = await self.store.get(trade_id)
        if row is None:
            return dict(status="NOT_FOUND")
        meta = json.loads(row["payload"])
        if meta.get("strategy") != "spot_futures":
            raise ValueError("CASH_TRADE_SCOPE_MISMATCH")
        from types import SimpleNamespace

        p = SimpleNamespace(**meta["cash_plan"])
        s, f = self._clients(p)
        readers = {
            p.venue + ":spot": SpotOrderReader(p.venue + ":spot", s),
            p.venue: FutureOrderReader(p.venue, f),
        }
        _, unresolved = await reconcile_and_persist(self.diary, readers, trade_id)
        if unresolved:
            return dict(
                status="HOLD", reason="CASH_ORDER_UNRESOLVED", unresolved=unresolved
            )
        try:
            flow = await self._flow(trade_id, p)
            proof = await self._private(p, flow)
            return dict(
                status="PRIVATE_VERIFIED",
                phase=row["phase"],
                cashflow=flow.row(),
                proof=proof,
                replay_authorized=False,
            )
        except Exception as error:
            return dict(status="HOLD", reason=str(error), replay_authorized=False)

    async def close(self, trade_id, reason="OPERATOR_EXIT"):
        async with self.lock:
            row = await self.store.get(trade_id)
            if not row or row["phase"] != "CASH_OPEN":
                return dict(status="RECONCILE_REQUIRED", trade_id=trade_id)
            from types import SimpleNamespace

            p = SimpleNamespace(**json.loads(row["payload"])["cash_plan"])
            try:
                return await self._close(trade_id, p, "CASH_OPEN", reason)
            except Exception as error:
                return await self._hold(trade_id, "CASH_CLOSE_PREFLIGHT:" + str(error))

    async def recover(self, trade_id):
        """Explicit bounded recovery after read-only terminal/private reconciliation.

        Never resumes an entry or resends a claimed exit. Each recovery has new
        intent IDs and an atomic round claim; callers must request it explicitly.
        """
        async with self.lock:
            observation = await self.reconcile(trade_id)
            if observation.get("status") != "PRIVATE_VERIFIED":
                return observation
            row = await self.store.get(trade_id)
            if not row or row["phase"] not in (
                "CASH_HOLD",
                "CASH_ACCOUNTING_PENDING",
                "CASH_EXIT_SUBMITTING",
                "CASH_FUTURE_SUBMITTING",
                "CASH_SPOT_SUBMITTING",
                "CASH_RECOVERY_RESERVED",
                "CASH_SPOT_TERMINAL",
                "CASH_ENTRY_PARTIAL",
            ):
                return dict(status="RECONCILE_REQUIRED", trade_id=trade_id)
            meta = json.loads(row["payload"])
            n = meta.get("cash_recovery_round", 0)
            if type(n) is not int or not 0 <= n < 3:
                return await self._hold(trade_id, "CASH_RECOVERY_ROUND_LIMIT")
            from types import SimpleNamespace

            p = SimpleNamespace(**meta["cash_plan"])
            if not self.exit_authority(p.venue):
                return await self._hold(trade_id, "CASH_EXIT_AUTHORITY_REQUIRED")
            async with aiosqlite.connect(self.store.path) as d:
                await d.execute("BEGIN IMMEDIATE")
                cur = await d.execute(
                    "SELECT phase,payload FROM live_trades WHERE trade_id=?",
                    (trade_id,),
                )
                current = await cur.fetchone()
                if current != (row["phase"], row["payload"]):
                    return dict(status="RECONCILE_REQUIRED", trade_id=trade_id)
                meta["cash_recovery_round"] = n + 1
                await d.execute(
                    "UPDATE live_trades SET phase='CASH_RECOVERY_RESERVED',payload=?,updated_at=? WHERE trade_id=?",
                    (json.dumps(meta), self.clock(), trade_id),
                )
                await d.commit()
            try:
                return await self._close(
                    trade_id,
                    p,
                    "CASH_RECOVERY_RESERVED",
                    "EXPLICIT_RECOVERY",
                    ":recovery:" + str(n + 1),
                )
            except Exception as error:
                return await self._hold(
                    trade_id, "CASH_RECOVERY_PREFLIGHT:" + str(error)
                )

    async def finalize(self, trade_id):
        async with self.lock:
            from types import SimpleNamespace
            from .private_funding_reader import Reader as FundingReader
            from .spot_future_live_result import finalize

            row = await self.store.get(trade_id)
            if not row or row["phase"] != "CASH_ACCOUNTING_PENDING":
                return dict(status="RECONCILE_REQUIRED", trade_id=trade_id)
            meta = json.loads(row["payload"])
            p = SimpleNamespace(
                **meta["cash_plan"],
                persisted=meta["cash_plan"],
                closed_at=meta["cash_closed_at"],
                reason=meta["cash_close_reason"]
            )
            try:
                flow = await self._flow(trade_id, p)
                proof = await self._private(p, flow)
                _, f = self._clients(p)
                funding = await FundingReader(p.venue, f, clock=self.clock).collect(
                    p.future_symbol, p.opened_at, p.closed_at
                )
                if not funding.verified:
                    return dict(status="ACCOUNTING_PENDING", reason=funding.reason)
                committed = await finalize(
                    self.store.path, trade_id, p, flow, proof, funding, self.clock()
                )
                return dict(
                    status="CLOSED" if committed else "ALREADY_CLOSED",
                    trade_id=trade_id,
                    held_inventory_base=flow.spot_base,
                )
            except Exception as error:
                return dict(
                    status="ACCOUNTING_PENDING", reason=str(error), trade_id=trade_id
                )
