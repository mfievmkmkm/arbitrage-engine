"""Scanner -> fresh private/public admission -> durable IOC session.
No defaults enable trading. The operator's acceptance record is mandatory.
"""

import asyncio
import math
import time
import aiosqlite
from dataclasses import replace
from types import SimpleNamespace as NS
from .native_order_plan import prepare_pair, market
from .public_books import normalize
from .recovery_market import executable, Reader as RecoveryReader
from .executor_plan import ExecutionPlan, PlannedLeg
from .fee_schedule import FeeSchedule, FeeRate
from .safe_executor import SafeExecutor
from .ccxt_executor import CCXTExecutor
from .quote_order_evidence import validate
from .live_durable_session import Session
from .trade_journal import TradeJournal


class Coordinator:
    def __init__(
        self,
        durable,
        runtime,
        diary,
        public,
        private,
        snapshots,
        funding,
        authority,
        bankroll=50,
        notional=5,
        minimum_net=0.05,
        safety_pct=0.1,
        max_seconds=1200,
        clock=time.time,
        exit_authority=None,
    ):
        self.durable, self.runtime, self.diary = durable, runtime, diary
        self.public, self.private, self.snapshots, self.funding = (
            public,
            private,
            snapshots,
            funding,
        )
        self.authority, self.bankroll, self.notional, self.minimum = (
            authority,
            bankroll,
            notional,
            minimum_net,
        )
        self.safety, self.max_seconds, self.clock = safety_pct, max_seconds, clock
        self.exit_authority = exit_authority or authority
        self.lock = asyncio.Lock()
        self.latest = {"status": "NOT_STARTED"}
        self.halt = None

    def fresh_snapshot(self, snapshot, venues, flat=False):
        now = self.clock()
        for venue in venues:
            r = snapshot.get(venue, {})
            if not getattr(r.get("health"), "ok", False):
                raise ValueError("ENTRY_PRIVATE_UNTRUSTED")
            ts, received = r.get("snapshot_started_at"), r.get("fetched_at")
            if (
                any(
                    isinstance(t, bool) or t is None or not math.isfinite(t)
                    for t in (ts, received)
                )
                or not ts <= received <= now
                or now - ts > 15
            ):
                raise ValueError("ENTRY_PRIVATE_STALE")
            if flat and (r.get("positions") or r.get("orders")):
                raise ValueError("ENTRY_ACCOUNT_NOT_FLAT")
        return snapshot

    async def _prepare(self, op, require_authority=True):
        symbol, lv, sv = op["symbol"], op["buy"], op["sell"]
        venues = (lv, sv)
        if lv == sv or (require_authority and not self.authority(symbol, lv, sv)):
            raise ValueError("ENTRY_AUTHORITY_REQUIRED")
        if any(v not in self.private or v not in self.public for v in venues):
            raise ValueError("ENTRY_CLIENT_MISSING")
        if (
            not all(math.isfinite(x) and x > 0 for x in (self.bankroll, self.notional))
            or self.notional > 5
        ):
            raise ValueError("ENTRY_NOTIONAL_LIMIT")
        async with aiosqlite.connect(self.durable.path) as db:
            async with db.execute(
                "SELECT COALESCE(SUM(net),0) FROM live_results WHERE ts>=?",
                (math.floor(self.clock() / 86400) * 86400,),
            ) as cursor:
                daily_net = float((await cursor.fetchone())[0])
            async with db.execute(
                "SELECT COALESCE(SUM(net),0) FROM live_results"
            ) as cursor:
                total_net = float((await cursor.fetchone())[0])
        if not math.isfinite(daily_net) or daily_net <= -self.bankroll * 0.02:
            raise ValueError("ENTRY_DAILY_LOSS_LIMIT")
        equity = self.bankroll + total_net
        if not math.isfinite(equity) or equity <= 0:
            raise ValueError("ENTRY_REALIZED_EQUITY_LIMIT")
        budget_notional = min(self.notional, 5, equity * 0.1)
        snap = self.fresh_snapshot(await self.snapshots(), venues, flat=True)
        for v in venues:
            bal = snap[v]["balance"]
            free = float(bal.free)
            if (
                getattr(bal, "venue", None) != v
                or getattr(bal, "currency", None) != "USDT"
                or not math.isfinite(free)
                or free < budget_notional * 1.2
            ):
                raise ValueError("ENTRY_MARGIN_BUFFER_LOW")

        async def account(v):
            c = self.private[v]
            if (
                c.has.get("fetchPositionMode") is not True
                or c.has.get("fetchTradingFee") is not True
            ):
                raise ValueError("ENTRY_ACCOUNT_CAPABILITY_UNVERIFIED")
            mode, fee = await asyncio.gather(
                c.fetch_position_mode(symbol), c.fetch_trading_fee(symbol)
            )
            if mode.get("hedged") is not False:
                raise ValueError("ENTRY_ONE_WAY_REQUIRED")
            if fee.get("symbol") != symbol:
                raise ValueError("ENTRY_FEE_SCOPE_MISMATCH")
            rates = [fee.get(k) for k in ("maker", "taker")]
            if any(
                isinstance(x, bool)
                or x is None
                or not math.isfinite(float(x))
                or not 0 <= float(x) <= 0.1
                for x in rates
            ):
                raise ValueError("ENTRY_FEE_UNVERIFIED")
            return FeeRate(*map(float, rates))

        rates = await asyncio.wait_for(
            asyncio.gather(*(account(v) for v in venues)), 10
        )
        carry, known, _ = await self.funding.pair_carry_pct(
            lv, sv, symbol, self.max_seconds
        )
        if not known or not math.isfinite(carry):
            raise ValueError("ENTRY_FUNDING_UNKNOWN")

        async def book(v):
            start = self.clock()
            raw = await self.public[v].fetch_order_book(symbol, limit=20)
            return normalize(
                raw, symbol, start, self.clock(), 1.5, raw.get("data_source", "REST")
            )

        books = await asyncio.wait_for(asyncio.gather(*(book(v) for v in venues)), 8)
        sizes = [float(market(self.private[v], symbol)["contractSize"]) for v in venues]
        # Verify native/public metadata agree before interpreting contract depth.
        for v, size in zip(venues, sizes):
            if not math.isclose(
                float(market(self.public[v], symbol)["contractSize"]),
                size,
                rel_tol=1e-12,
            ):
                raise ValueError("ENTRY_PUBLIC_PRIVATE_UNITS_MISMATCH")
        qty = min(
            budget_notional / books[0]["asks"][0][0],
            budget_notional / books[1]["bids"][0][0],
        )
        native = prepare_pair(
            symbol,
            lv,
            sv,
            self.private[lv],
            self.private[sv],
            qty,
            books[0]["asks"][0][0],
            books[1]["bids"][0][0],
        )
        if not native.valid:
            raise ValueError(native.reason)
        prices = [
            executable(b[side], native.base_qty / size)[1]
            for b, side, size in zip(books, ("asks", "bids"), sizes)
        ]
        native = prepare_pair(
            symbol, lv, sv, self.private[lv], self.private[sv], native.base_qty, *prices
        )
        if not native.valid:
            raise ValueError(native.reason)
        reqs = {}
        for v, b, size, req in zip(venues, books, sizes, (native.long, native.short)):
            evidence = dict(
                source="PUBLIC_IOC_ENTRY_V1",
                venue=v,
                symbol=symbol,
                side=req.side,
                contracts=req.qty,
                contract_size=size,
                base_qty=req.qty * size,
                book_ts=b["timestamp"] / 1000,
                started_at=b["requested_at"],
                received_at=b["received_at"],
                bids=b["bids"],
                asks=b["asks"],
            )
            reqs[v] = replace(req, market_evidence=evidence)
            validate(reqs[v], v, self.clock())
        plan = ExecutionPlan(
            PlannedLeg(lv, symbol, "buy", native.long.qty, sizes[0], native.base_qty),
            PlannedLeg(sv, symbol, "sell", native.short.qty, sizes[1], native.base_qty),
            native.base_qty,
            True,
            "OK",
        )
        # Funding income is not credited; possible carry loss is reserved.
        allowance = native.base_qty * max(prices) * (self.safety + max(0, -carry)) / 100

        async def snapshots():
            return self.fresh_snapshot(await self.snapshots(), venues)

        return (
            plan,
            reqs,
            FeeSchedule(dict(zip(venues, rates))),
            allowance,
            snapshots,
            equity,
            max(0, -daily_net),
        )

    async def preview(self, op):
        """Read-only account/book/cost check, including with trading disabled.

        The normal process path always requires authority. Preview has no
        executor/session, never reserves capacity and cannot remove STOP.
        """
        from .entry_cost import estimate
        from .order_policy import choose

        async with self.lock:
            try:
                plan, requests, fees, allowance, _, equity, daily_loss = (
                    await self._prepare(op, require_authority=False)
                )
                long, short = requests[op["buy"]], requests[op["sell"]]
                edge = (short.price - long.price) / long.price * 100
                costs = estimate(
                    plan, choose(edge, 0.01, True), long.price, short.price, fees
                )
                threshold = self.minimum + allowance
                return dict(
                    status=(
                        "DATA_CHECKED"
                        if costs.net_edge_usd >= threshold
                        else "NET_BELOW_THRESHOLD"
                    ),
                    symbol=op["symbol"],
                    long_venue=op["buy"],
                    short_venue=op["sell"],
                    base_qty=plan.base_amount,
                    long_contracts=long.qty,
                    short_contracts=short.qty,
                    long_limit=long.price,
                    short_limit=short.price,
                    net_edge_usd=costs.net_edge_usd,
                    required_net_usd=threshold,
                    equity=equity,
                    daily_loss=daily_loss,
                    write_authorized=bool(
                        self.authority(op["symbol"], op["buy"], op["sell"])
                    ),
                    orders_sent=False,
                )
            except Exception as e:
                return dict(
                    status=str(e) if isinstance(e, ValueError) else "CHECK_UNAVAILABLE",
                    orders_sent=False,
                    write_authorized=False,
                )

    async def process(self, opportunities):
        async with self.lock:
            if await self.durable.active():
                self.latest = {"status": "ENTRY_DURABLE_CAPACITY"}
                return self.latest
            for op in opportunities:
                started = False
                try:
                    plan, requests, fees, allowance, snapshots, equity, daily_loss = (
                        await self._prepare(op)
                    )

                    def gate():
                        try:
                            if not self.authority(op["symbol"], op["buy"], op["sell"]):
                                return False
                            for v, r in requests.items():
                                validate(r, v, self.clock())
                            return True
                        except (ValueError, TypeError):
                            return False

                    executors = {
                        v: SafeExecutor(
                            v,
                            CCXTExecutor(v, self.private[v]),
                            self.diary,
                            gate,
                            exit_gate=lambda: self.exit_authority(
                                op["symbol"], op["buy"], op["sell"]
                            ),
                        )
                        for v in requests
                    }
                    long, short = requests[op["buy"]], requests[op["sell"]]
                    started = True
                    result = await Session(
                        self.durable, self.runtime, TradeJournal(self.diary)
                    ).open(
                        self.runtime.load(),
                        dict(
                            symbol=op["symbol"],
                            plan=plan,
                            long_executor=executors[op["buy"]],
                            short_executor=executors[op["sell"]],
                            long_price=long.price,
                            short_price=short.price,
                            edge_pct=(short.price - long.price) / long.price * 100,
                            book_spread_pct=0.01,
                            fee_schedule=fees,
                            min_net_edge_usd=self.minimum + allowance,
                            admission_kwargs=dict(
                                live_enabled=True,
                                release_gate=NS(micro_live=True),
                                startup_safe=True,
                                private_streams=True,
                                withdrawals_disabled=True,
                                bankroll=equity,
                                daily_loss=daily_loss,
                                books_fresh=True,
                                risk_ok=True,
                            ),
                            private_snapshot=snapshots,
                            long_round=lambda value: float(
                                self.private[op["buy"]].amount_to_precision(
                                    op["symbol"], value
                                )
                            ),
                            short_round=lambda value: float(
                                self.private[op["sell"]].amount_to_precision(
                                    op["symbol"], value
                                )
                            ),
                            prepared_requests=requests,
                            recovery_market_reader=RecoveryReader(self.public),
                        ),
                        dict(
                            plan=plan,
                            symbol=op["symbol"],
                            long_venue=op["buy"],
                            short_venue=op["sell"],
                            opened_at=self.clock(),
                        ),
                    )
                    self.latest = {
                        "status": result.reason,
                        "phase": result.phase,
                        "opened": result.ok,
                    }
                    if (
                        not result.ok
                        and result.result is not None
                        and getattr(result.result, "trade_id", "")
                        and self.halt
                    ):
                        self.halt("ENTRY_RECONCILIATION_REQUIRED")
                    return self.latest
                except (ValueError, KeyError, TypeError, RuntimeError) as error:
                    if started and self.halt:
                        self.halt("ENTRY_DISPATCH_UNKNOWN")
                    self.latest = {
                        "status": (
                            str(error)
                            if isinstance(error, ValueError) and not started
                            else "ENTRY_PREFLIGHT_BLOCKED"
                        )
                    }
                except asyncio.CancelledError:
                    if started and self.halt:
                        self.halt("ENTRY_DISPATCH_INTERRUPTED")
                    raise
                except Exception:
                    if started and self.halt:
                        self.halt("ENTRY_DISPATCH_UNKNOWN")
                    self.latest = {"status": "ENTRY_PREPARATION_UNAVAILABLE"}
            return self.latest
