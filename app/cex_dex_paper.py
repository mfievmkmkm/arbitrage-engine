"""Durable conservative DEX round trips. Bounds are model fills, never wallet fills."""

import asyncio
import copy
import json
import math
import time
import aiosqlite
from .ledger_store import SCHEMA as LEDGER_SCHEMA

SCHEMA = """
CREATE TABLE IF NOT EXISTS cex_dex_paper_state(id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER,payload TEXT);
CREATE TABLE IF NOT EXISTS cex_dex_paper(id INTEGER PRIMARY KEY,symbol TEXT,opened_at REAL,closed_at REAL,status TEXT,net REAL,payload TEXT);
CREATE TABLE IF NOT EXISTS cex_dex_paper_marks(id INTEGER PRIMARY KEY,position_id INTEGER,ts REAL,net REAL,payload TEXT);
CREATE TABLE IF NOT EXISTS cex_dex_paper_events(position_id INTEGER,venue TEXT,ts REAL,amount REAL,payload TEXT,PRIMARY KEY(position_id,venue,ts));
CREATE TABLE IF NOT EXISTS cex_dex_paper_decisions(id INTEGER PRIMARY KEY,ts REAL,reason TEXT,payload TEXT);
"""


def mark(p, x, h):
    sign = 1 if p["forward"] else -1
    dex_pnl = sign * (x["cash"] - p["entry_cash"])
    future_pnl = (
        (1 if p["cex_side"] == "buy" else -1)
        * p["base_qty"]
        * (x["price"] - p["entry_price"])
    )
    fees = p["base_qty"] * x["price"] * x["fee_rate"] + x["gas"]
    gross = dex_pnl + future_pnl
    return dict(
        ts=x["ts"],
        base_qty=p["base_qty"],
        gross=gross,
        entry_fees=p["entry_fees"],
        exit_fees=fees,
        safety=p["safety"],
        funding=h.amount,
        funding_known=h.verified,
        net=gross - p["entry_fees"] - fees - p["safety"] + h.amount,
        entry_gas=p["entry_gas"],
        exit_gas=x["gas"],
        entry_cash=p["entry_cash"],
        exit_cash=x["cash"],
        exit_gas_price=x["gas_price"],
        exit_gas_book=x["gas_book"],
        funding_events=h.events,
        funding_covered_until=h.covered_until,
        entry_price=p["entry_price"],
        exit_price=x["price"],
        exit_fee_rate=x["fee_rate"],
        entry_dex=p["entry_dex"],
        exit_dex=x["dex"],
        entry_cex=p["entry_cex"],
        exit_cex=x["cex"],
        asset_amount_raw=p["asset_amount_raw"],
        cex_contracts=p["cex_contracts"],
        mode="DEX_FIRM_PUBLIC_HISTORY_MODEL",
    )


class Engine:
    def __init__(
        self,
        path,
        source,
        capital=50,
        max_seconds=900,
        min_edge=0.05,
        trailing=0.2,
        clock=time.time,
    ):
        if (
            not all(
                math.isfinite(float(v))
                for v in (capital, max_seconds, min_edge, trailing)
            )
            or capital <= 0
            or max_seconds <= 0
            or min_edge < 0
            or not 0 < trailing < 1
        ):
            raise ValueError("DEX_PAPER_CONFIG_INVALID")
        (
            self.path,
            self.source,
            self.capital,
            self.max_seconds,
            self.min_edge,
            self.trailing,
            self.clock,
        ) = (str(path), source, capital, max_seconds, min_edge, trailing, clock)
        self.state = dict(positions={}, next_id=1)
        self.version, self.pending = 0, 0
        self.lock = asyncio.Lock()
        self.budget = lambda: self.capital
        self.external_reserved = lambda: 0
        self.allow_open = lambda p: True
        self.on_closed = None

    @property
    def positions(self):
        return self.state["positions"]

    @property
    def used_capital(self):
        return self.pending + sum(p["reserve"] for p in self.positions.values())

    async def init(self):
        async with aiosqlite.connect(self.path) as d:
            await d.executescript(SCHEMA + LEDGER_SCHEMA)
            await d.execute(
                "INSERT OR IGNORE INTO cex_dex_paper_state VALUES(1,0,?)",
                (json.dumps(self.state),),
            )
            await d.commit()
            async with d.execute(
                "SELECT version,payload FROM cex_dex_paper_state WHERE id=1"
            ) as c:
                self.version, payload = await c.fetchone()
            self.state = json.loads(payload)

    async def cycle(self, route=None, entry_enabled=True):
        async with self.lock:
            try:
                return await self._cycle(route, entry_enabled)
            finally:
                self.pending = 0

    async def _cycle(self, route, entry_enabled):
        state = copy.deepcopy(self.state)
        saved, marks, events, closed, decisions = [], [], [], [], []
        for key, p in list(state["positions"].items()):
            pending = p["status"] == "EXIT_ACCOUNTING_PENDING"
            until = p["exit_at"] if pending else self.clock()
            # Historical IO precedes the fresh actionable exit quotes.
            h = await self.source.settlements(p, until)
            x = p["exit_quote"] if pending else await self.source.exit(p)
            if not x.get("ok"):
                p["data_reason"] = x.get("reason", "DEX_EXIT_UNAVAILABLE")
                saved.append(p)
                continue
            if not pending:
                h = self.source.extend_history(p, h, x["ts"])
            if x["ts"] < p.get("last_mark", {}).get("ts", p["opened_at"]):
                p["data_reason"] = "DEX_MARK_TIME_REGRESSION"
                saved.append(p)
                continue
            m = mark(p, x, h)
            marks.append((p["id"], m))
            p.update(
                last_mark=m, net=m["net"], funding_reason=h.reason, data_reason="OK"
            )
            if h.verified:
                events.extend((p["id"], e) for e in h.events)
            price_net = m["gross"] - m["entry_fees"] - m["exit_fees"] - m["safety"]
            p["best_net"] = max(p.get("best_net", 0), m["net"] if h.verified else 0)
            reason = (
                p.get("exit_reason")
                if pending
                else (
                    "TIME_STOP"
                    if x["ts"] - p["opened_at"] >= self.max_seconds
                    else (
                        "NET_STOP"
                        if price_net
                        <= -max(p["entry_cash"], p["base_qty"] * p["entry_price"])
                        * 0.02
                        else (
                            "NET_CAPTURE"
                            if h.verified and m["net"] >= p["entry_edge"] * 0.7
                            else (
                                "TRAILING"
                                if h.verified
                                and p["best_net"] > 0
                                and m["net"] <= p["best_net"] * (1 - self.trailing)
                                else None
                            )
                        )
                    )
                )
            )
            if reason:
                p.update(
                    status="EXIT_ACCOUNTING_PENDING",
                    exit_at=x["ts"],
                    exit_quote=x,
                    exit_reason=reason,
                )
                if h.verified and h.covered_until >= x["ts"]:
                    p.update(status="CLOSED", closed_at=x["ts"])
                    del state["positions"][key]
                    closed.append(copy.deepcopy(p))
            saved.append(p)
        if (
            route is not None
            and entry_enabled
            and not state["positions"]
            and not closed
        ):
            reason = "SHARED_CAPITAL_LOW"
            if self.used_capital + self.external_reserved() + 12 <= self.budget():
                # Make the reservation visible to the other Paper modules during IO.
                self.pending = 12
                x = await self.source.entry(route)
                reason = x.get("reason", "DEX_ENTRY_UNAVAILABLE")
                if x.get("ok"):
                    p = x["position"]
                    if p["entry_edge"] < self.min_edge:
                        reason = "DEX_MODEL_EDGE_LOW"
                    elif not self.allow_open(p):
                        reason = "RISK_OR_VENUE_DISABLED"
                    elif self.external_reserved() + p["reserve"] > self.budget():
                        reason = "SHARED_CAPITAL_LOW"
                    else:
                        pid = state["next_id"]
                        state["next_id"] += 1
                        p.update(
                            id=pid, closed_at=None, status="OPEN", net=0, best_net=0
                        )
                        state["positions"][str(pid)] = p
                        saved.append(p)
                        reason = "DEX_PAPER_OPEN"
            # Never persist the route: it contains the wallet address.
            decisions.append(
                (
                    reason,
                    dict(
                        label=route.get("label", "DEX"),
                        mode="DEX_FIRM_PUBLIC_HISTORY_MODEL",
                    ),
                )
            )
            state["last_decision"] = reason
        async with aiosqlite.connect(self.path) as d:
            await d.execute("BEGIN IMMEDIATE")
            cursor = await d.execute(
                "UPDATE cex_dex_paper_state SET version=version+1,payload=? WHERE id=1 AND version=?",
                (json.dumps(state), self.version),
            )
            if cursor.rowcount != 1:
                raise ValueError("DEX_PAPER_CONCURRENT_WRITER")
            for p in saved:
                await d.execute(
                    "INSERT INTO cex_dex_paper VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET closed_at=excluded.closed_at,status=excluded.status,net=excluded.net,payload=excluded.payload",
                    (
                        p["id"],
                        p["symbol"],
                        p["opened_at"],
                        p["closed_at"],
                        p["status"],
                        p["net"],
                        json.dumps(p),
                    ),
                )
            for pid, m in marks:
                await d.execute(
                    "INSERT INTO cex_dex_paper_marks(position_id,ts,net,payload) VALUES(?,?,?,?)",
                    (pid, m["ts"], m["net"], json.dumps(m)),
                )
            for pid, e in events:
                async with d.execute(
                    "SELECT payload FROM cex_dex_paper_events WHERE position_id=? AND venue=? AND ts=?",
                    (pid, e["venue"], e["ts"]),
                ) as c:
                    old = await c.fetchone()
                if old and json.loads(old[0]) != e:
                    raise ValueError("DEX_FUNDING_MODEL_EVENT_CONFLICT")
                await d.execute(
                    "INSERT OR IGNORE INTO cex_dex_paper_events VALUES(?,?,?,?,?)",
                    (pid, e["venue"], e["ts"], e["amount"], json.dumps(e)),
                )
            for reason, row in decisions:
                await d.execute(
                    "INSERT INTO cex_dex_paper_decisions(ts,reason,payload) VALUES(?,?,?)",
                    (self.clock(), reason, json.dumps(row)),
                )
            for p in closed:
                await d.execute(
                    "INSERT INTO ledger VALUES(?,?,?,?,?,?)",
                    (
                        p["closed_at"],
                        "CEX_DEX_PAPER_NET",
                        "dex:" + str(p["id"]),
                        p["cex_venue"],
                        p["net"],
                        "DEX_FIRM_PUBLIC_HISTORY_MODEL",
                    ),
                )
            await d.commit()
        self.state, self.version = state, self.version + 1
        self.pending = 0
        if self.on_closed:
            for p in closed:
                await self.on_closed(p)
        return closed


class Cycle:
    def __init__(self, paper, routes):
        self.paper, self.routes, self.index, self.entry_enabled = paper, routes, 0, True

    async def cycle(self):
        route = self.routes[self.index % len(self.routes)] if self.routes else None
        self.index += 1
        await self.paper.cycle(route, self.entry_enabled)
        rows = [
            dict(
                strategy="cex_dex",
                symbol=p["symbol"],
                ts=self.paper.clock(),
                paper_allowed=True,
                live_allowed=False,
                evidence_mode="DEX_FIRM_PUBLIC_HISTORY_MODEL",
                status=p["status"],
                net=p["net"],
                reason=p.get("data_reason", "PAPER_MODEL_OPEN"),
            )
            for p in self.paper.positions.values()
        ]
        if not rows and route is not None and self.entry_enabled:
            rows.append(
                dict(
                    strategy="cex_dex",
                    symbol=route.get("label", "DEX"),
                    ts=self.paper.clock(),
                    reason=self.paper.state.get("last_decision", "PAPER_IDLE"),
                    evidence_mode="DEX_FIRM_PUBLIC_HISTORY_MODEL",
                    paper_allowed=False,
                    live_allowed=False,
                )
            )
        return rows
