"""Continuous read-only reconciliation and exit observation.
The reconciliation core does not submit orders. Its optional on_update hook
may invoke a separately authorized write-side coordinator in main.
"""

import asyncio, json, math, time, logging
from .durable_order_reconcile import reconcile_and_persist, TERMINAL
from .live_monitor_store import Store as MonitorStore
from .live_recovery_evidence import (
    rebuild,
    exit_accounting,
    Unverified,
    validate_trade,
    validate_sides,
)
from .runtime_state import RuntimeTrade
from .runtime_private_reconcile import verify_trade
from .private_residual import verify as verify_flat
from .dynamic_exit import ExitState, decide
from .private_funding_reader import Evidence


class Monitor:
    def __init__(
        self,
        durable,
        runtime,
        diary,
        snapshot_source,
        order_readers,
        supervisor,
        stop,
        market_reader=None,
        funding_reader=None,
        interval=10,
        max_private_age=15,
        max_seconds=1200,
        target_capture=0.7,
        trailing=0.2,
        clock=time.time,
        on_update=None,
        cash_observer=None,
        stop_net=None,
    ):
        self.durable = durable
        self.runtime = runtime
        self.diary = diary
        self.snapshot_source = snapshot_source
        self.order_readers = order_readers
        self.supervisor = supervisor
        self.stop = stop
        self.market = market_reader
        self.funding = funding_reader
        self.interval = interval
        self.max_private_age = max_private_age
        self.max_seconds = max_seconds
        self.target = target_capture
        self.trailing = trailing
        self.clock = clock
        self.on_update = on_update
        self.cash_observer = cash_observer
        self.stop_net = stop_net
        self.store = MonitorStore(durable.path)
        self.lock = asyncio.Lock()
        self.task = None
        self.latest = None
        self.stopping = asyncio.Event()
        self.wake = asyncio.Event()

    async def init(self):
        await self.store.init()
        self.latest = await self.store.latest()
        if self.latest:
            self.latest["private_verified"] = False
            self.latest["reconciled"] = False
            for row in self.latest.get("trades", []):
                row["private_verified"] = False

    def _incident(self, code, trade_id="", severity="CRITICAL", **details):
        scope = ":".join(
            str(details.get(k, ""))
            for k in ("venue", "symbol", "intent_id", "order_id")
        )
        return {
            "key": trade_id + ":" + code + ":" + scope,
            "trade_id": trade_id,
            "code": code,
            "severity": severity,
            "details": details,
        }

    def _fresh(self, snapshot, venues):
        now = self.clock()
        for venue in venues:
            row = snapshot.get(venue) or {}
            stamp = row.get("snapshot_started_at")
            fetched = row.get("fetched_at")
            if (
                not getattr(row.get("health"), "ok", False)
                or stamp is None
                or fetched is None
            ):
                return False
            if not all(math.isfinite(float(x)) for x in (stamp, fetched)):
                return False
            if (
                stamp > fetched
                or fetched > now + 1
                or now - stamp > self.max_private_age
            ):
                return False
        return bool(venues)

    async def _coverage(self, trade, until, mark=False):
        if self.funding is None:
            return Evidence(False, 0, (), "FUNDING_EVIDENCE_REQUIRED")
        try:
            reader = (
                self.funding.mark
                if mark and hasattr(self.funding, "mark")
                else self.funding.collect
            )
            result = await reader(trade, until)
            if not math.isfinite(result.amount):
                return Evidence(False, 0, (), "FUNDING_AMOUNT_INVALID")
            if result.verified and result.covered_until + 1e-6 < until:
                return Evidence(
                    False,
                    result.amount,
                    result.events,
                    "FUNDING_COVERAGE_GAP",
                    result.covered_until,
                )
            await self.store.settlements(trade.trade_id, result.events)
            return result
        except Exception:
            return Evidence(False, 0, (), "FUNDING_EVIDENCE_UNAVAILABLE")

    async def cycle(self):
        async with self.lock:
            return await self._cycle()

    async def _cycle(self):
        states, unresolved = await reconcile_and_persist(self.diary, self.order_readers)
        intents = await self.diary.order_intents()
        rows = await self.durable.active()
        cash_rows = [
            r
            for r in rows
            if json.loads(r["payload"]).get("strategy")
            in ("spot_futures", "spot_spot", "cex_dex")
        ]
        rows = [r for r in rows if r not in cash_rows]
        snapshot = await self.snapshot_source()
        now = self.clock()
        incidents = []
        marks = []
        positions = []
        closed = []
        venues = set(snapshot)
        for row in rows:
            venues.update((row.get("long_venue"), row.get("short_venue")))
        venues.discard(None)
        private_ok = self._fresh(snapshot, venues)
        for iid in unresolved:
            from .durable_order_reconcile import accounting_missing

            meta = intents.get(iid) or {}
            if meta.get("state") in TERMINAL and accounting_missing(meta):
                incidents.append(
                    self._incident(
                        "FILL_ACCOUNTING_MISSING",
                        meta.get("trade_id", ""),
                        intent_id=iid,
                    )
                )
            incidents.append(
                self._incident(
                    "UNRESOLVED_ORDER",
                    (intents.get(iid) or {}).get("trade_id", ""),
                    intent_id=iid,
                )
            )
        owners = {}
        for row in cash_rows:
            meta = json.loads(row["payload"])
            if meta.get("strategy") == "cex_dex":
                plan = meta.get("dex_live_plan") or {}
                if plan.get("venue") and plan.get("symbol"):
                    owners.setdefault((plan["venue"], plan["symbol"]), []).append(
                        row["trade_id"]
                    )
                continue
            plan = meta.get("cash_plan") or {}
            if plan.get("venue") and plan.get("future_symbol"):
                owners.setdefault((plan["venue"], plan["future_symbol"]), []).append(
                    row["trade_id"]
                )
        for row in rows:
            for venue in (row.get("long_venue"), row.get("short_venue")):
                if venue:
                    owners.setdefault((venue, row["symbol"]), []).append(
                        row["trade_id"]
                    )
        duplicates = {key for key, ids in owners.items() if len(ids) > 1}
        for key in duplicates:
            incidents.append(
                self._incident("DUPLICATE_EXPOSURE_OWNER", venue=key[0], symbol=key[1])
            )
        known_orders = {
            (x["venue"], x.get("order_id"))
            for x in intents.values()
            if x.get("order_id")
        }
        unmanaged = set()
        for venue, data in snapshot.items():
            if not getattr(data.get("health"), "ok", False):
                continue
            for position in data.get("positions", []):
                if not math.isfinite(float(position.qty)):
                    incidents.append(
                        self._incident("PRIVATE_QUANTITY_INVALID", venue=venue)
                    )
                    continue
                if abs(position.qty) > 1e-10 and (venue, position.symbol) not in owners:
                    incidents.append(
                        self._incident(
                            "UNMANAGED_POSITION", venue=venue, symbol=position.symbol
                        )
                    )
                    unmanaged.add((venue, position.symbol))
            for order in data.get("orders", []):
                if (venue, order.order_id) not in known_orders:
                    incidents.append(
                        self._incident(
                            "UNMANAGED_WORKING_ORDER",
                            venue=venue,
                            order_id=order.order_id,
                        )
                    )
                    unmanaged.add((venue, order.symbol))
        for row in rows:
            tid = row["trade_id"]
            payload = json.loads(row["payload"])
            hold = payload.get("entry_hold_reason") or payload.get("exit_hold_reason")
            if hold:
                incidents.append(self._incident(hold, tid, severity="HIGH"))
            lv = row.get("long_venue")
            sv = row.get("short_venue")
            symbol = row.get("symbol")
            info = {
                "trade_id": tid,
                "strategy": payload.get("strategy", "futures_futures"),
                "symbol": symbol,
                "phase": row["phase"],
                "long_venue": lv,
                "short_venue": sv,
                "private_verified": False,
                "recovery_effects": payload.get("recovery_effects"),
                "recovery_assessment": payload.get("recovery_assessment"),
            }
            positions.append(info)
            if not lv or not sv or not symbol or lv == sv:
                incidents.append(self._incident("DURABLE_METADATA_INVALID", tid))
                continue
            if not self._fresh(snapshot, (lv, sv)):
                incidents.append(self._incident("PRIVATE_SNAPSHOT_UNTRUSTED", tid))
                continue
            if (lv, symbol) in duplicates or (sv, symbol) in duplicates:
                continue
            own = [x for x in intents.values() if x["trade_id"] == tid]
            if any(x["symbol"] != symbol or x["venue"] not in (lv, sv) for x in own):
                incidents.append(self._incident("ORDER_SCOPE_MISMATCH", tid))
                continue
            if any(
                x["state"] not in TERMINAL or x["intent_id"] in unresolved for x in own
            ):
                continue
            try:
                validate_sides(own, lv, sv)
            except Unverified as error:
                incidents.append(self._incident(str(error), tid))
                continue
            # No working order on the route can be ignored, even after a terminal lookup.
            working = [
                o
                for v in (lv, sv)
                for o in snapshot[v].get("orders", [])
                if o.symbol == symbol
            ]
            if working:
                incidents.append(
                    self._incident("WORKING_ORDER_REQUIRES_RECONCILIATION", tid)
                )
                continue
            flat = verify_flat(snapshot, symbol, lv, sv).flat
            filled = any(float(x.get("filled") or 0) > 0 for x in own)
            if not own and not flat:
                incidents.append(self._incident("ORDER_EVIDENCE_MISSING", tid))
                continue
            if (
                flat
                and not filled
                and row["phase"] in ("PLANNED", "ENTRY_SUBMITTING", "UNKNOWN")
            ):
                try:
                    if any(
                        x.get("fee") is None
                        or not math.isfinite(float(x["fee"]))
                        or float(x["fee"]) != 0
                        for x in own
                    ):
                        raise ValueError()
                except (ValueError, TypeError):
                    incidents.append(
                        self._incident("ZERO_FILL_ACCOUNTING_REQUIRED", tid)
                    )
                    continue
                await self.durable.phase(
                    tid, "ABORTED", reason="PRIVATE_FLAT_NO_FILLED_INTENTS"
                )
                info["phase"] = "ABORTED"
                info["private_verified"] = True
                continue
            trade = None
            from .reduced_fill_accounting import reduction, basis, check_runtime, cycle

            reduced = any(reduction(x, tid) for x in own)
            if payload.get("runtime_trade"):
                try:
                    trade = RuntimeTrade(**payload["runtime_trade"])
                    validate_trade(trade)
                except (TypeError, ValueError):
                    incidents.append(self._incident("RUNTIME_PAYLOAD_INVALID", tid))
                    continue
                if (
                    trade.trade_id != tid
                    or trade.symbol != symbol
                    or trade.long_venue != lv
                    or trade.short_venue != sv
                ):
                    incidents.append(
                        self._incident("RUNTIME_PAYLOAD_SCOPE_MISMATCH", tid)
                    )
                    continue
                if reduced:
                    try:
                        check_runtime(trade, basis(row, payload, own))
                    except Unverified as error:
                        incidents.append(self._incident(str(error), tid))
                        continue
                elif (
                    trade.recovery_gross
                    or trade.recovery_fees
                    or trade.recovery_capital
                ):
                    incidents.append(self._incident("REDUCED_EVIDENCE_MISSING", tid))
                    continue
            if flat:
                protective_cycle = trade is None and any(
                    bool(x["reduce_only"]) and float(x.get("filled") or 0) > 0
                    for x in own
                )
                if trade is None and not reduced and not protective_cycle:
                    incidents.append(
                        self._incident(
                            "FLAT_ENTRY_ACCOUNTING_REQUIRED", tid, severity="HIGH"
                        )
                    )
                    continue
                try:
                    if reduced or protective_cycle:
                        trade, calculate = cycle(row, payload, own)
                    else:
                        calculate = exit_accounting(trade, own)
                except Unverified as error:
                    incidents.append(self._incident(str(error), tid, severity="HIGH"))
                    continue
                flat_at = float(payload.get("private_flat_at") or now)
                if not payload.get("private_flat_at"):
                    await self.durable.phase(tid, row["phase"], private_flat_at=flat_at)
                coverage = await self._coverage(trade, flat_at)
                info.update(
                    private_verified=True,
                    private_flat=True,
                    funding_reason=coverage.reason,
                )
                if not coverage.verified:
                    incidents.append(
                        self._incident(
                            "CLOSE_ACCOUNTING_PENDING",
                            tid,
                            severity="WARNING",
                            reason=coverage.reason,
                        )
                    )
                    continue
                result = calculate(coverage.amount)
                proof = {
                    "private_flat": True,
                    "orders_terminal": True,
                    "funding_verified": True,
                    "snapshot_ts": min(
                        snapshot[lv]["snapshot_started_at"],
                        snapshot[sv]["snapshot_started_at"],
                    ),
                    "funding_covered_until": coverage.covered_until,
                }
                if await self.store.finalize(tid, result.__dict__, proof):
                    closed.append(result.__dict__)
                info["phase"] = "CLOSED_PRIVATE_VERIFIED"
                continue
            if trade is None:
                try:
                    trade = rebuild(row, payload, own, snapshot)
                except Unverified as error:
                    incidents.append(self._incident(str(error), tid))
                    continue
                await self.durable.phase(
                    tid,
                    "HEDGED_PRIVATE_VERIFIED",
                    runtime_trade=trade.row(),
                    actual_long=trade.long_contracts,
                    actual_short=trade.short_contracts,
                    long_price=trade.long_entry,
                    short_price=trade.short_entry,
                    fees=trade.entry_fees,
                )
                await self.durable.phase(
                    tid,
                    "OPEN",
                    reason="RECONSTRUCTED_FROM_TERMINAL_FILLS_AND_PRIVATE_STATE",
                )
                payload["runtime_trade"] = trade.row()
                info["phase"] = "OPEN"
            if row["phase"] == "EXIT_SUBMITTING" and not hold:
                from .exit_residual_evidence import evaluate as residual_evidence

                try:
                    residual = residual_evidence(
                        trade, row, own, snapshot, now, self.max_private_age
                    )
                except (ValueError, TypeError, AttributeError, KeyError) as error:
                    completed = payload.get(
                        "exit_residual_completed_at",
                        payload.get("exit_dispatch_completed_at"),
                    )
                    waiting = (
                        str(error) == "RESIDUAL_PRIVATE_FILL_MISMATCH"
                        and not isinstance(completed, bool)
                        and isinstance(completed, (int, float))
                        and math.isfinite(completed)
                        and 0 <= now - completed < 30
                    )
                    incidents.append(
                        self._incident(
                            (
                                "EXIT_PRIVATE_SETTLEMENT_PENDING"
                                if waiting
                                else "EXIT_RESIDUAL_REQUIRES_RECOVERY"
                            ),
                            tid,
                            severity="WARNING" if waiting else "HIGH",
                            reason=(
                                str(error)
                                if isinstance(error, ValueError)
                                else "RESIDUAL_PRIVATE_UNTRUSTED"
                            ),
                        )
                    )
                else:
                    info.update(
                        private_verified=True,
                        base_qty=trade.base_qty,
                        opened_at=trade.opened_at,
                        exit_signal="EXIT_RESIDUAL",
                        residual_long_contracts=residual.long_contracts,
                        residual_short_contracts=residual.short_contracts,
                    )
                    incidents.append(
                        self._incident(
                            "EXIT_RESIDUAL_VERIFIED", tid, severity="WARNING"
                        )
                    )
                continue
            match = verify_trade(trade, snapshot)
            if not match.safe:
                incidents.append(self._incident(match.reason, tid))
                continue
            info.update(
                private_verified=True,
                base_qty=trade.base_qty,
                opened_at=trade.opened_at,
            )
            if row["phase"] == "EXIT_SUBMITTING":
                incidents.append(
                    self._incident(
                        "EXIT_RESIDUAL_REQUIRES_RECOVERY", tid, severity="HIGH"
                    )
                )
                continue
            if row["phase"] == "HEDGED_PRIVATE_VERIFIED":
                await self.durable.mark_open(tid, reason="PRIVATE_RECONCILED")
                info["phase"] = "OPEN"
            coverage = await self._coverage(trade, now, mark=True)
            market = (
                await self.market.mark(trade)
                if self.market is not None
                else {"ok": False, "reason": "EXIT_MARKET_SOURCE_MISSING"}
            )
            hold_seconds = self.max_seconds
            if payload.get("strategy") == "funding_arb":
                hold_seconds = payload.get("funding_plan", {}).get(
                    "hold_seconds", self.max_seconds
                )
                if (
                    type(hold_seconds) not in (int, float)
                    or not math.isfinite(hold_seconds)
                    or not 60 <= hold_seconds <= 86400
                ):
                    incidents.append(self._incident("FUNDING_HOLD_CONFIG_INVALID", tid))
                    continue
            age = now - trade.opened_at
            if not market.get("ok"):
                info["exit_signal"] = (
                    "TIME_STOP" if age >= hold_seconds else "DATA_UNAVAILABLE"
                )
                incidents.append(
                    self._incident(
                        "EXIT_MARKET_UNVERIFIED",
                        tid,
                        severity="WARNING",
                        reason=market.get("reason", "UNKNOWN"),
                    )
                )
                continue
            gross = (
                (market["long_exit"] - trade.long_entry)
                + (trade.short_entry - market["short_exit"])
            ) * trade.base_qty + trade.recovery_gross
            fee = market.get("exit_fee")
            estimate = (
                None
                if fee is None
                else gross
                - trade.entry_fees
                - trade.recovery_fees
                - fee
                + coverage.amount
            )
            best = float(payload.get("best_net", -1e18))
            edge = payload.get("entry_net_edge_usd")
            signal = "TIME_STOP" if age >= hold_seconds else "HOLD"
            costs_verified = bool(market.get("fees_verified") and coverage.verified)
            target_basis = None
            if trade.recovery_capital > 0 and costs_verified and fee is not None:
                ceiling = max(
                    0,
                    (trade.short_entry - trade.long_entry) * trade.base_qty
                    + trade.recovery_gross
                    - trade.entry_fees
                    - trade.recovery_fees
                    - fee
                    + coverage.amount,
                )
                edge = min(float(edge), ceiling) if edge is not None else ceiling
                target_basis = "REDUCED_FULL_CONVERGENCE_CEILING_MODEL"
            if costs_verified and estimate is not None:
                state = ExitState(
                    float(edge) if edge is not None else 1e99, trade.opened_at, best
                )
                decision = decide(
                    state,
                    now,
                    estimate,
                    self.target,
                    self.trailing,
                    hold_seconds,
                    stop_net=self.stop_net,
                )
                signal = decision.reason if decision.close else "HOLD"
                best = state.best_net
            # A conservative price/fee stop needs no forecast funding credit.
            if (
                self.stop_net is not None
                and fee is not None
                and market.get("fees_verified")
                and gross - trade.entry_fees - trade.recovery_fees - fee
                <= self.stop_net
            ):
                signal = "NET_STOP"
            mark = {
                "trade_id": tid,
                "strategy": payload.get("strategy", "futures_futures"),
                "strategy": payload.get("strategy", "futures_futures"),
                "symbol": symbol,
                "estimated_net": estimate,
                "gross": gross,
                "exit_fee": fee,
                "funding": coverage.amount,
                "funding_known": coverage.verified,
                "costs_verified": costs_verified,
                "fees_verified": bool(market.get("fees_verified")),
                "exit_signal": signal,
                "spread": market["spread"],
                "market_ts": market["ts"],
                "target_edge_basis": target_basis,
                "target_net_model": (
                    max(0, float(edge) * self.target) if target_basis else None
                ),
            }
            marks.append(mark)
            info.update(mark)
            await self.durable.mark_open(
                tid, best_net=best, last_exit_signal=signal, last_mark=mark
            )
        # Rebuild JSON from authoritative rows; do not erase legacy positions without DB ownership.
        for row in cash_rows:
            tid = row["trade_id"]
            try:
                if self.cash_observer is None:
                    raise ValueError("CASH_MONITOR_REQUIRED")
                info = await self.cash_observer.observe(row)
            except Exception as error:
                info = dict(
                    trade_id=tid,
                    symbol=row["symbol"],
                    strategy=json.loads(row["payload"]).get("strategy"),
                    phase=row["phase"],
                    long_venue=row["long_venue"],
                    short_venue=row["short_venue"],
                    private_verified=False,
                    cash_error=str(error),
                )
            positions.append(info)
            if not info.get("private_verified"):
                private_ok = False
                incidents.append(
                    self._incident(
                        (
                            "DEX_SETTLEMENT_PENDING"
                            if info.get("settlement_pending") is True
                            else "CASH_PRIVATE_UNVERIFIED"
                        ),
                        tid,
                        severity=(
                            "WARNING"
                            if info.get("settlement_pending") is True
                            else "HIGH"
                        ),
                        reason=info.get("cash_error", "UNKNOWN"),
                    )
                )
            elif row["phase"] not in ("CASH_OPEN", "SS_OPEN", "DEX_OPEN"):
                incidents.append(
                    self._incident(
                        "CASH_RECONCILED_PENDING",
                        tid,
                        severity="WARNING",
                        reason=row["phase"],
                    )
                )
            if info.get("cash_market_error"):
                incidents.append(
                    self._incident(
                        "CASH_EXIT_MARKET_UNVERIFIED",
                        tid,
                        severity="WARNING",
                        reason=info["cash_market_error"],
                    )
                )
            if info.get("market_ts") is not None:
                marks.append(info)
        active = await self.durable.active()
        restored = await self.durable.runtime_trades()
        try:
            cached = self.runtime.load()
        except (ValueError, OSError):
            cached = []
        for trade in cached:
            dbrow = await self.durable.get(trade.trade_id)
            if dbrow is None:
                incidents.append(
                    self._incident("RUNTIME_WITHOUT_DURABLE", trade.trade_id)
                )
                restored.append(trade)
        self.runtime.save(restored)
        severe = [x for x in incidents if x["severity"] in ("CRITICAL", "HIGH")]
        summary = {
            "ts": now,
            "mode": "READ_ONLY_MONITOR",
            "release_authorized": False,
            "private_verified": private_ok,
            "reconciled": private_ok and not severe,
            "unknown_orders": len(unresolved),
            "active_db": len(active),
            "trades": [
                x
                for x in positions
                if x["phase"]
                not in ("ABORTED", "CLOSED_PRIVATE_VERIFIED", "CLOSED_WITH_INVENTORY")
            ],
            "marks": marks,
            "incidents": incidents,
            "closed": closed,
            "realized": await self.store.totals(),
            "runtime_trades": [x.row() for x in restored],
            "cash_inventory": (
                await self.cash_observer.inventory() if self.cash_observer else []
            ),
        }
        new = await self.store.publish(summary, incidents, marks)
        self.latest = summary
        self.supervisor.private_verified = private_ok
        self.supervisor.unknown_orders = bool(unresolved)
        self.supervisor.restart_clean = summary["reconciled"]
        self.supervisor.book_fresh = bool(marks) and all(
            self.clock() - x["market_ts"] <= getattr(self.market, "max_age", 1.5)
            for x in marks
        )
        self.supervisor.fee_verified = bool(marks) and all(
            x["fees_verified"] for x in marks
        )
        self.supervisor.funding_known = bool(marks) and all(
            x["funding_known"] for x in marks
        )
        if severe:
            reason = "LIVE_MONITOR:" + severe[0]["code"]
            if not self.stop.stopped or self.stop.reason != reason:
                self.stop.stop(reason)
            self.supervisor.kill.trip_global(reason)
        if self.on_update:
            await self.on_update(summary, new)
        return summary

    async def _run(self):
        while not self.stopping.is_set():
            self.wake.clear()
            try:
                await asyncio.wait_for(self.cycle(), timeout=max(30, self.interval * 3))
            except asyncio.CancelledError:
                raise
            except Exception:
                self.supervisor.private_verified = False
                self.supervisor.restart_clean = False
                self.supervisor.kill.trip_global("LIVE_MONITOR_FAILURE")
                self.stop.stop("LIVE_MONITOR_FAILURE")
                previous = self.latest or {}
                self.latest = {
                    "ts": self.clock(),
                    "mode": "READ_ONLY_MONITOR",
                    "release_authorized": False,
                    "private_verified": False,
                    "reconciled": False,
                    "unknown_orders": "не проверено",
                    "active_db": previous.get("active_db", "не проверено"),
                    "realized": previous.get("realized", {}),
                    "trades": [
                        dict(x, private_verified=False)
                        for x in previous.get("trades", [])
                    ],
                    "incidents": [self._incident("LIVE_MONITOR_FAILURE")],
                }
                try:
                    await self.store.publish(self.latest, self.latest["incidents"], [])
                except Exception:
                    pass
                logging.getLogger("arbitrage").exception("Live monitor failed")
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass

    def request_cycle(self):
        self.wake.set()

    async def start(self):
        if self.task is None:
            self.stopping.clear()
            self.task = asyncio.create_task(self._run())

    async def stop_task(self):
        if self.task:
            self.stopping.set()
            self.wake.set()
            try:
                await asyncio.wait_for(
                    asyncio.shield(self.task), timeout=max(35, self.interval * 3 + 5)
                )
            except asyncio.TimeoutError:
                self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
