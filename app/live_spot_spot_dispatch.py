"""Spot/Spot monitor/entry integration with independent exchange write authority."""

import asyncio
import json
import math
import uuid
import aiosqlite
from .dynamic_exit import ExitState, decide
from .live_decision_diary import record as record_decision


class Coordinator:
    def __init__(self, session, max_seconds=1200, target=0.7, trailing=0.2):
        self.session = session
        self.clock = session.clock
        self.max_seconds, self.target, self.trailing = max_seconds, target, trailing
        self.latest = dict(status="NOT_STARTED")

    async def process_rows(self, rows):
        if await self.session.store.active():
            self.latest = dict(status="GLOBAL_LIVE_CAPACITY")
            return self.latest
        for row in sorted(rows, key=lambda x: x.get("net", 0), reverse=True)[:5]:
            self.latest = await self.session.enter(row, "ss-" + uuid.uuid4().hex[:20])
            await record_decision(
                self.session.diary, "spot_spot", row, self.latest, self.clock
            )
            if self.latest["status"] != "BLOCKED":
                break
        return self.latest

    async def observe(self, row):
        p = self.session._plan(row)
        info = dict(
            trade_id=row["trade_id"],
            symbol=p.symbol,
            strategy="spot_spot",
            phase=row["phase"],
            long_venue=p.venues[0] + ":spot",
            short_venue=p.venues[1] + ":spot",
            private_verified=False,
            exit_signal="HOLD",
        )
        obs = await self.session.reconcile(row["trade_id"])
        if obs["status"] != "PRIVATE_VERIFIED":
            return dict(info, cash_error=obs.get("reason", "SS_PRIVATE_UNTRUSTED"))
        flow = obs["cashflow"]
        info.update(private_verified=True, cashflow=flow, cash_private=obs["proof"])
        if row["phase"] != "SS_OPEN":
            return info
        if self.clock() - p.opened_at >= self.max_seconds:
            info["exit_signal"] = "TIME_STOP"
        try:
            rates = dict(
                zip(
                    p.venues,
                    await asyncio.gather(
                        *(self.session._fee(v, p.symbol) for v in p.venues)
                    ),
                )
            )
            requests = []
            for v in p.venues:
                delta = flow["base"][v]
                if delta == 0:
                    continue
                side = "sell" if delta > 0 else "buy"
                qty = delta / (1 + rates[v]) if delta > 0 else (-delta) / (1 - rates[v])
                r = await self.session._request(v, p.symbol, side, qty)
                requests.append((v, r))
            # Mark remaining surplus at zero; reserve all costs for restoring deficits.
            net = sum(flow["cash"].values())
            for v, r in requests:
                net += (
                    1 if r.side == "sell" else -1
                ) * r.qty * r.price - r.qty * r.price * rates[v]
                if r.side == "buy":
                    restored = r.qty * (1 - rates[v])
                    net -= max(0, -flow["base"][v] - restored) * r.price
            if not requests or not math.isfinite(net):
                raise ValueError("SS_EXIT_ESTIMATE_INVALID")
            stamp = min(r.market_evidence["book_ts"] for _, r in requests)
            if not 0 <= self.clock() - stamp <= 1.5:
                raise ValueError("SS_EXIT_BOOK_STALE")
            meta = json.loads(row["payload"])
            state = ExitState(
                p.entry_edge, p.opened_at, float(meta.get("cash_best_net", -1e18))
            )
            decision = decide(
                state,
                self.clock(),
                net,
                self.target,
                self.trailing,
                self.max_seconds,
                stop_net=-self.session.bankroll * 0.01,
            )
            info.update(
                estimated_net=net,
                market_ts=stamp,
                funding=0,
                funding_known=True,
                fees_verified=True,
                exit_signal=decision.reason if decision.close else "HOLD",
                cash_best_net=state.best_net,
            )
        except Exception as e:
            info["cash_market_error"] = str(e)
        return info

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
            if info.get("strategy") != "spot_spot" or not info.get("private_verified"):
                continue
            if info["phase"] == "SS_OPEN":
                if info.get("market_ts") is not None:
                    # CAS ensures a stale mark cannot reopen a reserved exit.
                    async with aiosqlite.connect(self.session.store.path) as d:
                        await d.execute("BEGIN IMMEDIATE")
                        cur = await d.execute(
                            "SELECT phase,payload FROM live_trades WHERE trade_id=?",
                            (info["trade_id"],),
                        )
                        row = await cur.fetchone()
                        if row and row[0] == "SS_OPEN":
                            meta = json.loads(row[1])
                            meta.update(
                                cash_best_net=info["cash_best_net"], cash_last_mark=info
                            )
                            await d.execute(
                                "UPDATE live_trades SET payload=?,updated_at=? WHERE trade_id=?",
                                (json.dumps(meta), self.clock(), info["trade_id"]),
                            )
                            await d.commit()
                if info.get("exit_signal") in {
                    "TARGET_CAPTURE",
                    "NET_TRAILING",
                    "NET_STOP",
                    "TIME_STOP",
                }:
                    self.latest = await self.session.close(
                        info["trade_id"], info["exit_signal"]
                    )
            elif info["phase"] == "SS_ACCOUNTING_PENDING":
                try:
                    r = await self.session.finalize(info["trade_id"])
                except Exception as e:
                    self.latest = dict(status="ACCOUNTING_PENDING", reason=str(e))
                else:
                    if r["status"] == "CLOSED":
                        closed.append(r["result"])
        return closed


class CashObserver:
    def __init__(self, spot_future, spot_spot):
        self.spot_future, self.spot_spot = spot_future, spot_spot

    async def observe(self, row):
        strategy = json.loads(row["payload"])["strategy"]
        return await (
            self.spot_spot if strategy == "spot_spot" else self.spot_future
        ).observe(row)

    async def inventory(self):
        return await self.spot_future.inventory()
