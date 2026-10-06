"""Continuous read-only reconciliation and exit observation.
No exchange submit, cancel, approval or signing method is called by this service.
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
        self.store = MonitorStore(durable.path)
        self.lock = asyncio.Lock()
        self.task = None
        self.latest = None
        self.stopping = asyncio.Event()

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

    async def _coverage(self, trade, until):
        if self.funding is None:
            return Evidence(False, 0, (), "FUNDING_EVIDENCE_REQUIRED")
        try:
            result = await self.funding.collect(trade, until)
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
            incidents.append(
                self._incident(
                    "UNRESOLVED_ORDER",
                    (intents.get(iid) or {}).get("trade_id", ""),
                    intent_id=iid,
                )
            )
        owners = {}
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
            lv = row.get("long_venue")
            sv = row.get("short_venue")
            symbol = row.get("symbol")
            info = {
                "trade_id": tid,
                "symbol": symbol,
                "phase": row["phase"],
                "long_venue": lv,
                "short_venue": sv,
                "private_verified": False,
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
                await self.durable.phase(
                    tid, "ABORTED", reason="PRIVATE_FLAT_NO_FILLED_INTENTS"
                )
                info["phase"] = "ABORTED"
                info["private_verified"] = True
                continue
            trade = None
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
            if flat:
                if trade is None:
                    incidents.append(
                        self._incident(
                            "FLAT_ENTRY_ACCOUNTING_REQUIRED", tid, severity="HIGH"
                        )
                    )
                    continue
                try:
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
                await self.durable.phase(tid, "OPEN", reason="PRIVATE_RECONCILED")
                info["phase"] = "OPEN"
            market = (
                await self.market.mark(trade)
                if self.market is not None
                else {"ok": False, "reason": "EXIT_MARKET_SOURCE_MISSING"}
            )
            age = now - trade.opened_at
            if not market.get("ok"):
                info["exit_signal"] = (
                    "TIME_STOP" if age >= self.max_seconds else "DATA_UNAVAILABLE"
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
            coverage = await self._coverage(trade, now)
            gross = (
                (market["long_exit"] - trade.long_entry)
                + (trade.short_entry - market["short_exit"])
            ) * trade.base_qty
            fee = market.get("exit_fee")
            estimate = (
                None
                if fee is None
                else gross - trade.entry_fees - fee + coverage.amount
            )
            best = float(payload.get("best_net", -1e18))
            edge = payload.get("entry_net_edge_usd")
            signal = "TIME_STOP" if age >= self.max_seconds else "HOLD"
            costs_verified = bool(market.get("fees_verified") and coverage.verified)
            if costs_verified and estimate is not None:
                state = ExitState(
                    float(edge) if edge is not None else 1e99, trade.opened_at, best
                )
                decision = decide(
                    state, now, estimate, self.target, self.trailing, self.max_seconds
                )
                signal = decision.reason if decision.close else "HOLD"
                best = state.best_net
            mark = {
                "trade_id": tid,
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
            }
            marks.append(mark)
            info.update(mark)
            await self.durable.phase(
                tid, "OPEN", best_net=best, last_exit_signal=signal, last_mark=mark
            )
        # Rebuild JSON from authoritative rows; do not erase legacy positions without DB ownership.
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
                if x["phase"] not in ("ABORTED", "CLOSED_PRIVATE_VERIFIED")
            ],
            "marks": marks,
            "incidents": incidents,
            "closed": closed,
            "realized": await self.store.totals(),
            "runtime_trades": [x.row() for x in restored],
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
                await asyncio.wait_for(self.stopping.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass

    async def start(self):
        if self.task is None:
            self.stopping.clear()
            self.task = asyncio.create_task(self._run())

    async def stop_task(self):
        if self.task:
            self.stopping.set()
            try:
                await asyncio.wait_for(
                    asyncio.shield(self.task), timeout=max(35, self.interval * 3 + 5)
                )
            except asyncio.TimeoutError:
                self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
