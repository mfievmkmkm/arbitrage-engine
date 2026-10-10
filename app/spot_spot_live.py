"""Pre-positioned two-account cash round trip; no transfers, loans or withdrawals.

Every send has a durable stage claim and terminal order evidence. Restart reads;
only an explicit bounded recovery may restore the original asset allocation.
"""

import asyncio
import json
import math
import time
from decimal import Decimal
from types import SimpleNamespace as NS
from dataclasses import replace
import aiosqlite
from .spot_future_live_session import Session as CashSession
from .spot_native_order import market, validate_request
from .spot_future_native_plan import down, rate, spot_close
from .native_order_plan import number, format_value
from .spot_account_reader import Reader
from .spot_executor import SpotOrderReader
from .spot_future_live_preflight import quote
from .exchange_executor import SubmitRequest
from .durable_order_reconcile import reconcile_and_persist, TERMINAL
from .quote_order_evidence import validate


def rebuild(intents, tid, p):
    if not intents:
        return dict(
            base={v: 0.0 for v in p.venues},
            cash={v: 0.0 for v in p.venues},
            fees=0.0,
            base_fee_usd=0.0,
            base_fees=0.0,
        )
    seq = [r.get("_journal_sequence") for r in intents.values()]
    if any(type(x) is not int or x <= 0 for x in seq) or len(set(seq)) != len(seq):
        raise ValueError("SS_JOURNAL_SEQUENCE_UNKNOWN")
    base = {v: Decimal(0) for v in p.venues}
    cash = dict(base)
    fees = base_fees = base_fee_usd = Decimal(0)
    seen = set()
    for iid, r in sorted(intents.items(), key=lambda x: x[1]["_journal_sequence"]):
        v = r.get("venue", "").removesuffix(":spot")
        if (
            r.get("intent_id") != iid
            or r.get("trade_id") != tid
            or r.get("venue") != v + ":spot"
            or v not in base
            or r.get("symbol") != p.symbol
            or r.get("reduce_only") not in (0, False)
            or r.get("side") not in ("buy", "sell")
        ):
            raise ValueError("SS_INTENT_SCOPE_MISMATCH")
        if r.get("state") not in TERMINAL:
            raise ValueError("SS_ORDER_UNRESOLVED")
        q = number(r.get("filled"), "SS_FILLED", positive=False)
        if q > number(r.get("qty"), "SS_REQUESTED"):
            raise ValueError("SS_OVERFILL")
        fee, bf = r.get("fee"), r.get("base_fee")
        if any(
            isinstance(x, bool) or x is None or not math.isfinite(float(x))
            for x in (fee, bf)
        ):
            raise ValueError("SS_FEE_UNKNOWN")
        if r.get("base_currency") != p.base or abs(float(bf)) > float(q):
            raise ValueError("SS_FEE_UNITS_INVALID")
        fee, bf = Decimal(str(fee)), Decimal(str(bf))
        if q == 0:
            if fee or bf:
                raise ValueError("SS_ZERO_FILL_COST_CONFLICT")
            continue
        key = (v, r.get("order_id"))
        if not key[1] or key in seen:
            raise ValueError("SS_DUPLICATE_ORDER")
        seen.add(key)
        price = number(r.get("avg_price"), "SS_PRICE")
        sign = 1 if r["side"] == "buy" else -1
        base[v] += sign * q - bf
        cash[v] -= sign * q * price + fee
        fees += fee
        base_fees += bf
        base_fee_usd += bf * price
        if v == p.venues[0] and base[v] < Decimal("-1e-15"):
            raise ValueError("SS_CREDITED_INVENTORY_EXCEEDED")
        if base[v] + Decimal(str(p.baseline[v][p.base]["total"])) < 0:
            raise ValueError("SS_SOLD_UNOWNED_INVENTORY")
    return dict(
        base={v: float(x) for v, x in base.items()},
        cash={v: float(x) for v, x in cash.items()},
        fees=float(fees),
        base_fee_usd=float(base_fee_usd),
        base_fees=float(base_fees),
    )


class Session(CashSession):
    def __init__(
        self,
        durable,
        diary,
        clients,
        entry_authority,
        exit_authority,
        halt=None,
        bankroll=50,
        notional=5,
        minimum_net=0.05,
        safety_pct=0.1,
        clock=time.time,
    ):
        super().__init__(
            durable, diary, None, entry_authority, exit_authority, halt, clock
        )
        self.clients = clients
        self.bankroll, self.notional, self.minimum, self.safety = (
            bankroll,
            notional,
            minimum_net,
            safety_pct,
        )

    async def _hold(self, tid, reason):
        await self.store.phase(tid, "SS_HOLD", cash_hold_reason=reason)
        if self.halt:
            self.halt(reason)
        return dict(status="HOLD", reason=reason, trade_id=tid)

    def _plan(self, row):
        meta = json.loads(row["payload"])
        if meta.get("strategy") != "spot_spot":
            raise ValueError("SS_TRADE_SCOPE_MISMATCH")
        p = NS(**meta["spot_spot_plan"])
        if len(p.venues) != 2 or len(set(p.venues)) != 2 or p.symbol != row["symbol"]:
            raise ValueError("SS_PLAN_SCOPE_INVALID")
        return p

    async def _flow(self, tid, p):
        return rebuild(await self.diary.order_intents(tid), tid, p)

    async def _private(self, p, flow):
        snaps = await asyncio.gather(
            *(
                Reader(v + ":spot", self.clients[v], clock=self.clock).snapshot(
                    [p.base]
                )
                for v in p.venues
            )
        )
        stamps = []
        for v, snap in zip(p.venues, snaps):
            snap.fresh(self.clock())
            if snap.orders:
                raise ValueError("SS_WORKING_ORDER_REQUIRES_RECONCILIATION")
            for currency, delta in (
                (p.base, flow["base"][v]),
                ("USDT", flow["cash"][v]),
            ):
                original = p.baseline[v][currency]["total"]
                expected = float(Decimal(str(original)) + Decimal(str(delta)))
                actual = snap.asset(currency)
                tolerance = max(1e-12, math.ulp(expected) * 4, abs(delta) * 1e-8)
                if delta and abs(delta) < math.ulp(float(original)) * 8:
                    raise ValueError("SS_BALANCE_RESOLUTION_UNVERIFIED")
                if expected < 0 or abs(actual["total"] - expected) > tolerance:
                    raise ValueError("SS_PRIVATE_CASHFLOW_MISMATCH")
                if currency == p.base and actual["free"] + tolerance < max(
                    0, flow["base"][v]
                ):
                    raise ValueError("SS_OWNED_BASE_NOT_FREE")
            stamps.append(snap.started_at)
        return dict(
            verified=True,
            snapshot_ts=min(stamps),
            base_deltas=flow["base"],
            cash_deltas=flow["cash"],
        )

    async def _fee(self, v, symbol):
        c = self.clients[v]
        if c.has.get("fetchTradingFee") is not True:
            raise ValueError("SS_FEE_CAPABILITY_UNVERIFIED")
        f = await asyncio.wait_for(c.fetch_trading_fee(symbol), 8)
        if f.get("symbol") != symbol:
            raise ValueError("SS_FEE_SCOPE_MISMATCH")
        return float(rate(f.get("taker")))

    async def _request(self, v, symbol, side, qty):
        c = self.clients[v]
        raw = await asyncio.wait_for(c.fetch_order_book(symbol, limit=20), 8)
        ref = raw["asks" if side == "buy" else "bids"][0][0]
        q = down(c, symbol, number(qty, "SS_QUANTITY"))
        price = format_value(c, symbol, number(ref, "SS_PRICE"), "price")
        r = SubmitRequest(symbol, side, float(q), "limit", float(price), False, True)
        r = await quote(c, v + ":spot", r, 1, True, self.clock)
        validate_request(c, r)
        return r

    async def prepare(self, op):
        symbol, a, b = op["symbol"], op["buy"], op["sell"]
        if a == b or any(v not in self.clients for v in (a, b)):
            raise ValueError("SS_ROUTE_INVALID")
        ms = [market(self.clients[v], symbol) for v in (a, b)]
        if ms[0]["base"] != ms[1]["base"]:
            raise ValueError("SS_ASSET_IDENTITY_MISMATCH")
        base = ms[0]["base"]
        budget = min(
            float(number(self.notional, "SS_NOTIONAL")),
            float(number(self.bankroll, "SS_CAPITAL")) * 0.1,
        )
        if budget > 5:
            raise ValueError("SS_MICRO_BUDGET_LIMIT")
        minimum = float(number(self.minimum, "SS_MIN_NET", positive=False))
        safety = float(number(self.safety, "SS_SAFETY", positive=False))
        fees = dict(
            zip((a, b), await asyncio.gather(*(self._fee(v, symbol) for v in (a, b))))
        )
        snaps = await asyncio.gather(
            *(
                Reader(v + ":spot", self.clients[v], clock=self.clock).snapshot([base])
                for v in (a, b)
            )
        )
        if any(s.orders for s in snaps):
            raise ValueError("SS_ACCOUNT_WORKING_ORDERS")
        if any(s.asset("USDT")["free"] < budget * 1.2 for s in snaps):
            raise ValueError("SS_QUOTE_RESTORATION_BUFFER_LOW")
        baseline = {v: snap.balances for v, snap in zip((a, b), snaps)}
        buy_raw, sell_raw = await asyncio.gather(
            *(
                asyncio.wait_for(self.clients[v].fetch_order_book(symbol, limit=20), 8)
                for v in (a, b)
            )
        )
        requested = min(
            budget / buy_raw["asks"][0][0] / (1 + fees[a]),
            budget / sell_raw["bids"][0][0] / (1 + fees[b]),
        )
        first = await self._request(a, symbol, "buy", requested)
        credited = first.qty * (1 - fees[a])
        second = await self._request(b, symbol, "sell", credited / (1 + fees[b]))
        if snaps[1].asset(base)["free"] < second.qty * (1 + fees[b]):
            raise ValueError("SS_PREPOSITIONED_BASE_REQUIRED")
        # Convergence model, reserves four account taker fees and input/output dust.
        reserved = 2 * max(first.price, second.price) * first.qty * sum(fees.values())
        projected = (
            second.qty * second.price
            - first.qty * first.price
            - reserved
            - budget * safety / 100
        )
        if (
            max(
                first.qty * first.price * (1 + fees[a]),
                second.qty * second.price * (1 + fees[b]),
            )
            > budget
            or projected < minimum
        ):
            raise ValueError("SS_NET_BELOW_THRESHOLD")
        p = NS(
            symbol=symbol,
            base=base,
            venues=[a, b],
            baseline=baseline,
            fees=fees,
            opened_at=self.clock(),
            entry_edge=projected,
            budget=budget,
            reference=max(first.price, second.price),
        )
        for snap in snaps:
            snap.fresh(self.clock())
        for v, r in ((a, first), (b, second)):
            validate(r, v + ":spot", self.clock())
        return p, first, second

    async def preview(self, op):
        try:
            p, first, second = await self.prepare(op)
            return dict(
                status="DATA_CHECKED",
                orders_sent=False,
                net_edge=p.entry_edge,
                symbol=p.symbol,
                buy_qty=first.qty,
                sell_qty=second.qty,
            )
        except Exception as e:
            return dict(status="BLOCKED", reason=str(e), orders_sent=False)

    async def enter(self, op, tid):
        async with self.lock:
            try:
                if not all(self.entry_authority(v) for v in (op["buy"], op["sell"])):
                    raise ValueError("SS_ENTRY_AUTHORITY_REQUIRED")
                p, first, second = await self.prepare(op)
                async with aiosqlite.connect(self.store.path) as d:
                    cur = await d.execute(
                        "SELECT COALESCE(SUM(net),0),COALESCE(SUM(CASE WHEN ts>=? THEN net ELSE 0 END),0) FROM live_results",
                        (int(self.clock() // 86400) * 86400,),
                    )
                    total, daily = await cur.fetchone()
                if (
                    not all(math.isfinite(float(x)) for x in (total, daily))
                    or daily <= -self.bankroll * 0.02
                    or p.budget > min(5, (self.bankroll + total) * 0.1)
                ):
                    raise ValueError("SS_REALIZED_RISK_LIMIT")
                # Independent check of both venue gates after potentially slow private IO.
                if not all(self.entry_authority(v) for v in p.venues):
                    raise ValueError("SS_ENTRY_AUTHORITY_REQUIRED")
                if not await self.store.reserve_entry(
                    tid,
                    strategy="spot_spot",
                    symbol=p.symbol,
                    long_venue=p.venues[0] + ":spot",
                    short_venue=p.venues[1] + ":spot",
                    planned_long=first.qty,
                    planned_short=second.qty,
                    spot_spot_plan=vars(p),
                ):
                    raise ValueError("GLOBAL_LIVE_CAPACITY")
            except Exception as e:
                return dict(status="BLOCKED", reason=str(e))
            try:
                if not await self._claim(tid, "PLANNED", "SS_BUY_SUBMITTING"):
                    return await self._hold(tid, "SS_STAGE_CLAIM_CONFLICT")
                a, b = p.venues
                await self._send(
                    tid, "ss-buy", a + ":spot", self.clients[a], first, spot=True
                )
                flow = await self._flow(tid, p)
                await self._private(p, flow)
                await self.store.phase(tid, "SS_BUY_TERMINAL")
                if flow["base"][a] <= 0:
                    return await self._close(
                        tid, p, "SS_BUY_TERMINAL", "ENTRY_ZERO_FILL"
                    )
                if not all(self.entry_authority(v) for v in p.venues):
                    return await self._close(
                        tid, p, "SS_BUY_TERMINAL", "ENTRY_AUTHORITY_EXPIRED"
                    )
                try:
                    second = await self._request(
                        b, p.symbol, "sell", flow["base"][a] / (1 + p.fees[b])
                    )
                    projected = (
                        second.qty * second.price
                        + sum(flow["cash"].values())
                        - 2 * second.qty * p.reference * sum(p.fees.values())
                        - p.budget * self.safety / 100
                    )
                    if (
                        projected < self.minimum
                        or second.qty * (1 + p.fees[b]) > p.baseline[b][p.base]["free"]
                        or second.qty * second.price * (1 + p.fees[b]) > p.budget
                    ):
                        raise ValueError("SS_POST_FILL_NET_OR_INVENTORY_LIMIT")
                except Exception:
                    return await self._close(
                        tid, p, "SS_BUY_TERMINAL", "ENTRY_SELL_PREFLIGHT_FAILED"
                    )
                if not await self._claim(tid, "SS_BUY_TERMINAL", "SS_SELL_SUBMITTING"):
                    return await self._hold(tid, "SS_STAGE_CLAIM_CONFLICT")
                await self._send(
                    tid, "ss-sell", b + ":spot", self.clients[b], second, spot=True
                )
                flow = await self._flow(tid, p)
                proof = await self._private(p, flow)
                if (
                    abs(sum(flow["base"].values())) * p.reference > 0.05
                    or flow["base"][b] == 0
                ):
                    await self.store.phase(tid, "SS_ENTRY_PARTIAL")
                    return await self._close(
                        tid, p, "SS_ENTRY_PARTIAL", "ENTRY_PARTIAL"
                    )
                actual_edge = (
                    sum(flow["cash"].values())
                    - p.reference
                    * sum(abs(q) * p.fees[v] for v, q in flow["base"].items())
                    - p.budget * self.safety / 100
                )
                if not math.isfinite(actual_edge) or actual_edge < self.minimum:
                    await self.store.phase(
                        tid, "SS_ENTRY_PARTIAL", cash_actual_entry_edge=actual_edge
                    )
                    return await self._close(
                        tid, p, "SS_ENTRY_PARTIAL", "ACTUAL_ENTRY_NET_BELOW_THRESHOLD"
                    )
                await self.store.phase(
                    tid,
                    "SS_OPEN",
                    cashflow=flow,
                    cash_private=proof,
                    cash_actual_entry_edge=actual_edge,
                )
                return dict(status="OPEN", trade_id=tid, cashflow=flow)
            except (Exception, asyncio.CancelledError) as e:
                r = await asyncio.shield(self._hold(tid, "SS_ENTRY_UNKNOWN:" + str(e)))
                if isinstance(e, asyncio.CancelledError):
                    raise
                return r

    async def reconcile(self, tid):
        row = await self.store.get(tid)
        if not row:
            return dict(status="NOT_FOUND")
        try:
            p = self._plan(row)
            readers = {
                v + ":spot": SpotOrderReader(v + ":spot", self.clients[v])
                for v in p.venues
            }
            _, unresolved = await reconcile_and_persist(self.diary, readers, tid)
            if unresolved:
                raise ValueError("SS_ORDER_UNRESOLVED")
            flow = await self._flow(tid, p)
            proof = await self._private(p, flow)
            return dict(
                status="PRIVATE_VERIFIED",
                cashflow=flow,
                proof=proof,
                replay_authorized=False,
            )
        except Exception as e:
            return dict(status="HOLD", reason=str(e), replay_authorized=False)

    async def _close(self, tid, p, expected, reason, suffix=""):
        flow = await self._flow(tid, p)
        await self._private(p, flow)
        if not all(self.exit_authority(v) for v in p.venues):
            return await self._hold(tid, "SS_EXIT_AUTHORITY_REQUIRED")
        if not await self._claim(tid, expected, "SS_EXIT_SUBMITTING"):
            return dict(status="RECONCILE_REQUIRED", trade_id=tid)
        try:
            # Restore sold pre-positioned inventory before selling credited inventory.
            for side in ("buy", "sell"):
                for v in p.venues:
                    delta = flow["base"][v]
                    if (side == "buy" and delta >= 0) or (
                        side == "sell" and delta <= 0
                    ):
                        continue
                    fee = await self._fee(v, p.symbol)
                    qty = (-delta) / (1 - fee) if side == "buy" else delta / (1 + fee)
                    try:
                        req = await self._request(v, p.symbol, side, qty)
                    except ValueError as e:
                        if (
                            str(e)
                            not in (
                                "NATIVE_COST_MIN",
                                "NATIVE_AMOUNT_MIN",
                                "FORMATTED_AMOUNT_INVALID",
                            )
                            or abs(delta) * p.reference > 0.05
                        ):
                            raise
                        continue
                    await self._send(
                        tid,
                        "ss-exit-" + v + "-" + side + suffix,
                        v + ":spot",
                        self.clients[v],
                        req,
                        spot=True,
                        closing=True,
                    )
                    flow = await self._flow(tid, p)
                    await self._private(p, flow)
                    # Partial restoration holds; no blind continuation/repetition.
                    if side == "buy" and flow["base"][v] * p.reference < -0.05:
                        raise ValueError("SS_RESTORATION_PARTIAL")
            flow = await self._flow(tid, p)
            proof = await self._private(p, flow)
            if sum(abs(x) * p.reference for x in flow["base"].values()) > 0.10:
                raise ValueError("SS_EXIT_RESIDUAL")
            await self.store.phase(
                tid,
                "SS_ACCOUNTING_PENDING",
                cashflow=flow,
                cash_private=proof,
                cash_close_reason=reason,
                cash_closed_at=self.clock(),
            )
            return dict(status="ACCOUNTING_PENDING", trade_id=tid)
        except (Exception, asyncio.CancelledError) as e:
            r = await asyncio.shield(self._hold(tid, "SS_EXIT_UNKNOWN:" + str(e)))
            if isinstance(e, asyncio.CancelledError):
                raise
            return r

    async def close(self, tid, reason="OPERATOR_EXIT"):
        async with self.lock:
            row = await self.store.get(tid)
            if not row or row["phase"] != "SS_OPEN":
                return dict(status="RECONCILE_REQUIRED")
            try:
                return await self._close(tid, self._plan(row), "SS_OPEN", reason)
            except Exception as e:
                return await self._hold(tid, "SS_CLOSE_PREFLIGHT:" + str(e))

    async def recover(self, tid):
        async with self.lock:
            observed = await self.reconcile(tid)
            if observed.get("status") != "PRIVATE_VERIFIED":
                return observed
            row = await self.store.get(tid)
            if row["phase"] == "PLANNED":
                from .cash_reserved_abort import abort

                return await abort(self.store, row, self.clock())
            if row["phase"] not in {
                "SS_HOLD",
                "SS_ENTRY_PARTIAL",
                "SS_BUY_TERMINAL",
                "SS_BUY_SUBMITTING",
                "SS_SELL_SUBMITTING",
                "SS_EXIT_SUBMITTING",
                "SS_RECOVERY_RESERVED",
                "SS_ACCOUNTING_PENDING",
            }:
                return dict(status="RECONCILE_REQUIRED")
            meta = json.loads(row["payload"])
            n = meta.get("cash_recovery_round", 0)
            if type(n) is not int or not 0 <= n < 3:
                return await self._hold(tid, "SS_RECOVERY_ROUND_LIMIT")
            p = self._plan(row)
            if not all(self.exit_authority(v) for v in p.venues):
                return await self._hold(tid, "SS_EXIT_AUTHORITY_REQUIRED")
            async with aiosqlite.connect(self.store.path) as d:
                await d.execute("BEGIN IMMEDIATE")
                cur = await d.execute(
                    "SELECT phase,payload FROM live_trades WHERE trade_id=?", (tid,)
                )
                if await cur.fetchone() != (row["phase"], row["payload"]):
                    return dict(status="RECONCILE_REQUIRED")
                meta["cash_recovery_round"] = n + 1
                await d.execute(
                    "UPDATE live_trades SET phase='SS_RECOVERY_RESERVED',payload=?,updated_at=? WHERE trade_id=?",
                    (json.dumps(meta), self.clock(), tid),
                )
                await d.commit()
            try:
                return await self._close(
                    tid,
                    p,
                    "SS_RECOVERY_RESERVED",
                    "EXPLICIT_RECOVERY",
                    ":r" + str(n + 1),
                )
            except Exception as e:
                return await self._hold(tid, "SS_RECOVERY_PREFLIGHT:" + str(e))

    async def finalize(self, tid):
        async with self.lock:
            row = await self.store.get(tid)
            if not row or row["phase"] != "SS_ACCOUNTING_PENDING":
                return dict(status="RECONCILE_REQUIRED")
            p = self._plan(row)
            flow = await self._flow(tid, p)
            proof = await self._private(p, flow)
            # Small base deficits remain charged; surplus inventory has zero valuation.
            penalty = sum(max(0, -x) * p.reference for x in flow["base"].values())
            if sum(abs(x) * p.reference for x in flow["base"].values()) > 0.10:
                return dict(status="ACCOUNTING_PENDING", reason="SS_EXIT_RESIDUAL")
            net = sum(flow["cash"].values()) - penalty
            result = dict(
                trade_id=tid,
                symbol=p.symbol,
                strategy="spot_spot",
                gross=net + flow["fees"] + flow["base_fee_usd"],
                fees=flow["fees"] + flow["base_fee_usd"],
                funding=0,
                net=net,
                reason=json.loads(row["payload"])["cash_close_reason"],
                inventory_deltas=flow["base"],
                inventory_deficit_charge=penalty,
            )
            phase = (
                "CLOSED_WITH_INVENTORY"
                if any(flow["base"].values())
                else "CLOSED_PRIVATE_VERIFIED"
            )
            intents = await self.diary.order_intents(tid)
            async with aiosqlite.connect(self.store.path) as d:
                await d.execute("BEGIN IMMEDIATE")
                cur = await d.execute(
                    "SELECT phase,payload FROM live_trades WHERE trade_id=?", (tid,)
                )
                if await cur.fetchone() != (row["phase"], row["payload"]):
                    return dict(status="RECONCILE_REQUIRED")
                await d.execute(
                    "CREATE TABLE IF NOT EXISTS live_spot_allocations(trade_id TEXT,venue TEXT,base TEXT,baseline_total REAL,delta REAL,deficit_charge REAL,ts REAL,PRIMARY KEY(trade_id,venue,base))"
                )
                # Reject a changed journal after the private proof.
                d.row_factory = aiosqlite.Row
                cur = await d.execute(
                    "SELECT rowid AS _journal_sequence,* FROM order_intents WHERE trade_id=?",
                    (tid,),
                )
                current = {}
                for item in await cur.fetchall():
                    r = dict(item)
                    sequence = r["_journal_sequence"]
                    r.update(json.loads(r.get("payload") or "{}"))
                    r["_journal_sequence"] = sequence
                    current[r["intent_id"]] = r
                if current != intents:
                    return dict(
                        status="ACCOUNTING_PENDING", reason="SS_JOURNAL_CHANGED"
                    )
                meta = json.loads(row["payload"])
                meta.update(result=result, close_proof=proof)
                await d.execute(
                    "INSERT OR IGNORE INTO live_results VALUES(?,?,?,?,?,?,?,?)",
                    (
                        tid,
                        self.clock(),
                        result["gross"],
                        result["fees"],
                        0,
                        net,
                        result["reason"],
                        json.dumps(result),
                    ),
                )
                await d.execute(
                    "UPDATE live_trades SET phase=?,payload=?,updated_at=? WHERE trade_id=?",
                    (phase, json.dumps(meta), self.clock(), tid),
                )
                for v, delta in flow["base"].items():
                    await d.execute(
                        "INSERT OR IGNORE INTO live_spot_allocations VALUES(?,?,?,?,?,?,?)",
                        (
                            tid,
                            v,
                            p.base,
                            p.baseline[v][p.base]["total"],
                            delta,
                            max(0, -delta) * p.reference,
                            self.clock(),
                        ),
                    )
                for kind in ("TRADE_RESULT", "STATE"):
                    event = dict(result, kind=kind, phase=phase, proof=proof)
                    await d.execute(
                        "INSERT INTO execution_events(trade_id,ts,kind,reason,payload) VALUES(?,?,?,?,?)",
                        (tid, self.clock(), kind, result["reason"], json.dumps(event)),
                    )
                await d.commit()
            return dict(status="CLOSED", result=result)
