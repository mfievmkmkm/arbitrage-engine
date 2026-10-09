"""Main-facing cash lifecycle dispatcher; observe is strictly read-only."""

import asyncio
import json
import math
import time
import uuid
import aiosqlite
from types import SimpleNamespace as NS
from .spot_future_live_preflight import quote
from .spot_future_native_plan import spot_close, rate
from .exchange_executor import SubmitRequest
from .recovery_market import Reader as Quotes
from .private_funding_reader import Reader as Funding
from .dynamic_exit import ExitState, decide
from .live_decision_diary import record as record_decision


class Coordinator:
    def __init__(
        self, session, max_seconds=1200, target=0.7, trailing=0.2, clock=time.time
    ):
        self.session, self.clock = session, clock
        self.max_seconds, self.target, self.trailing = max_seconds, target, trailing
        self.latest = dict(status="NOT_STARTED")

    async def process_rows(self, rows):
        if await self.session.store.active():
            self.latest = dict(status="GLOBAL_LIVE_CAPACITY")
            return self.latest
        if any(not row.get("balance_covered") for row in await self.inventory()):
            self.latest = dict(status="CASH_INVENTORY_BALANCE_UNVERIFIED")
            return self.latest
        offers = sorted(
            (r for r in rows if r.get("direction") == "LONG_SPOT_SHORT_FUTURE"),
            key=lambda r: r.get("hypothetical_edge", 0),
            reverse=True,
        )
        for r in offers[:5]:
            if not self.session.entry_authority(r.get("exchange")):
                continue
            result = await self.session.enter(r, "sf-" + uuid.uuid4().hex[:20])
            self.latest = result
            await record_decision(self.session.diary,"spot_futures",r,result,self.clock)
            if result.get("status") != "BLOCKED":
                return result
        if not offers:
            self.latest = dict(status="NO_FORWARD_OPPORTUNITY")
        elif self.latest.get("status") == "NOT_STARTED":
            self.latest = dict(status="ENTRY_AUTHORITY_REQUIRED")
        return self.latest

    async def observe(self, row):
        """Terminal/private truth + optional fresh exit estimate, no exchange writes."""
        tid = row["trade_id"]
        meta = json.loads(row["payload"])
        p = NS(**meta["cash_plan"])
        info = dict(
            trade_id=tid,
            strategy="spot_futures",
            symbol=p.future_symbol,
            spot_symbol=p.spot_symbol,
            phase=row["phase"],
            long_venue=p.venue + ":spot",
            short_venue=p.venue,
            private_verified=False,
            exit_signal="HOLD",
        )
        observed = await self.session.reconcile(tid)
        if observed.get("status") != "PRIVATE_VERIFIED":
            return dict(
                info, cash_error=observed.get("reason", "CASH_PRIVATE_UNTRUSTED")
            )
        info.update(
            private_verified=True,
            cashflow=observed["cashflow"],
            cash_private=observed["proof"],
        )
        if row["phase"] != "CASH_OPEN":
            return info
        flow = await self.session._flow(tid, p)
        s, f = self.session._clients(p)
        now = self.clock()
        info["exit_signal"] = (
            "TIME_STOP" if now - p.opened_at >= self.max_seconds else "HOLD"
        )
        try:
            sf, ff = await asyncio.wait_for(
                asyncio.gather(
                    s.fetch_trading_fee(p.spot_symbol),
                    f.fetch_trading_fee(p.future_symbol),
                ),
                8,
            )
            if sf.get("symbol") != p.spot_symbol or ff.get("symbol") != p.future_symbol:
                raise ValueError("CASH_EXIT_FEE_SCOPE_MISMATCH")
            sr, fr = float(rate(sf.get("taker"))), float(rate(ff.get("taker")))
            until = self.clock() - 30
            evidence = await Funding(p.venue, f, clock=self.clock).collect(
                p.future_symbol, p.opened_at, until
            )
            calendar = await self.session.admission.funding.get(
                p.venue, p.future_symbol
            )
            next_ts = getattr(calendar, "next_ts", None)
            interval = getattr(calendar, "interval_hours", None)
            from .native_order_plan import number

            next_ts = float(number(next_ts, "FUNDING_TIMESTAMP"))
            next_ts = next_ts / 1000 if next_ts > 10_000_000_000 else next_ts
            interval = float(number(interval, "FUNDING_INTERVAL")) * 3600
            gap_known = (
                (getattr(calendar, "exchange", None), getattr(calendar, "symbol", None))
                == (p.venue, p.future_symbol)
                and next_ts > self.clock()
                and next_ts - interval <= until
            )
            known = evidence.verified and gap_known
            raw = await asyncio.wait_for(s.fetch_order_book(p.spot_symbol, limit=20), 8)
            spot = spot_close(s, p.spot_symbol, flow.spot_base, raw["bids"][0][0], sr)
            req = SubmitRequest(
                p.future_symbol,
                "buy",
                flow.future_base / p.contract_size,
                "market",
                reduce_only=True,
            )
            spot, future = await asyncio.gather(
                quote(s, p.venue + ":spot", spot, 1, True, self.clock),
                Quotes({p.venue: f}, clock=self.clock).quote(
                    p.venue, req, p.contract_size
                ),
            )
            close_fees = (
                spot.qty * spot.price * sr
                + flow.future_base * future.reference_price * fr
            )
            # All unsold inventory remains valued at zero in this estimate.
            estimated = (
                flow.spot_cash
                + spot.qty * spot.price
                + flow.future_realized
                + flow.future_base * (flow.future_entry_price - future.reference_price)
                - flow.quote_fees
                - close_fees
                + evidence.amount
            )
            if not math.isfinite(estimated):
                raise ValueError("CASH_EXIT_ESTIMATE_INVALID")
            stamp = min(
                spot.market_evidence["book_ts"], future.market_evidence["book_ts"]
            )
            if self.clock() - stamp > 1.5:
                raise ValueError("CASH_EXIT_BOOK_STALE")
            best = float(meta.get("cash_best_net", -1e18))
            if known:
                state = ExitState(
                    float(meta.get("cash_entry_edge", self.session.admission.minimum)),
                    p.opened_at,
                    best,
                )
                decision = decide(
                    state,
                    self.clock(),
                    estimated,
                    self.target,
                    self.trailing,
                    self.max_seconds,
                    stop_net=-self.session.admission.bankroll * 0.01,
                )
                best = state.best_net
                info["exit_signal"] = decision.reason if decision.close else "HOLD"
            info.update(
                estimated_net=estimated,
                funding_known=known,
                fees_verified=True,
                funding_reason=evidence.reason if not known else "VERIFIED",
                market_ts=stamp,
                cash_best_net=best,
                gross=estimated + close_fees - evidence.amount,
                exit_fee=close_fees,
                funding=evidence.amount,
            )
        except Exception as error:
            info["cash_market_error"] = str(error)
        return info

    async def persist_mark(self, info):
        if "market_ts" not in info:
            return
        async with aiosqlite.connect(self.session.store.path) as d:
            await d.execute("BEGIN IMMEDIATE")
            cur = await d.execute(
                "SELECT phase,payload FROM live_trades WHERE trade_id=?",
                (info["trade_id"],),
            )
            row = await cur.fetchone()
            if not row or row[0] != "CASH_OPEN":
                return
            payload = json.loads(row[1])
            payload.update(cash_best_net=info["cash_best_net"], cash_last_mark=info)
            await d.execute(
                "UPDATE live_trades SET payload=?,updated_at=? WHERE trade_id=?",
                (json.dumps(payload), self.clock(), info["trade_id"]),
            )
            await d.commit()

    async def process(self, summary):
        closed = []
        if (
            not summary.get("private_verified")
            or not summary.get("reconciled")
            or summary.get("unknown_orders")
            or not 0 <= self.clock() - summary.get("ts", 0) <= 15
        ):
            return closed
        for info in summary.get("trades", []):
            if info.get("strategy") != "spot_futures" or not info.get(
                "private_verified"
            ):
                continue
            await self.persist_mark(info)
            if info["phase"] == "CASH_OPEN" and info.get("exit_signal") in (
                "TIME_STOP",
                "TARGET_CAPTURE",
                "NET_TRAILING",
                "NET_STOP",
            ):
                self.latest = await self.session.close(
                    info["trade_id"], info["exit_signal"]
                )
            elif info["phase"] == "CASH_ACCOUNTING_PENDING":
                result = await self.session.finalize(info["trade_id"])
                if result.get("status") == "CLOSED":
                    row = await self.session.store.get(info["trade_id"])
                    closed.append(json.loads(row["payload"])["result"])
        return closed

    async def inventory(self):
        async with aiosqlite.connect(self.session.store.path) as d:
            cur = await d.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='live_cash_inventory'"
            )
            if not await cur.fetchone():
                return []
            d.row_factory = aiosqlite.Row
            cur = await d.execute(
                "SELECT trade_id,venue,base,qty,cost_usd FROM live_cash_inventory ORDER BY ts"
            )
            rows = [dict(r) for r in await cur.fetchall()]
        from .spot_account_reader import Reader

        grouped = {}
        for row in rows:
            grouped.setdefault(row["venue"], set()).add(row["base"])

        async def account(venue, currencies):
            try:
                client = self.session.admission.spot_clients[venue]
                return venue, await Reader(
                    venue + ":spot", client, clock=self.clock
                ).snapshot(currencies)
            except Exception:
                return venue, None

        snapshots = dict(
            await asyncio.gather(*(account(v, c) for v, c in grouped.items()))
        )
        totals = {}
        for row in rows:
            key = row["venue"], row["base"]
            totals[key] = totals.get(key, 0) + row["qty"]
        for row in rows:
            snapshot = snapshots.get(row["venue"])
            row["balance_covered"] = bool(
                snapshot
                and snapshot.asset(row["base"])["total"]
                >= totals[(row["venue"], row["base"])]
            )
        return rows
