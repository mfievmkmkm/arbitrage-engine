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
        dex_pnl=dex_pnl,
        cex_pnl=future_pnl,
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
        virtual=False,
        namespace=None,
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
        self.on_opened = None
        if type(virtual) is not bool:
            raise ValueError("DEX_PAPER_MODE_INVALID")
        self.virtual = virtual
        if namespace not in (None, "channel", "bybit", "paired") or (namespace and not virtual):
            raise ValueError("DEX_PAPER_NAMESPACE_INVALID")
        self.namespace = namespace

    def sql(self, statement):
        # Virtual observations must never become firm execution/OOS evidence.
        table = "cex_dex_virtual_paper" + ("_" + self.namespace if self.namespace else "")
        return statement.replace("cex_dex_paper", table) if self.virtual else statement

    @property
    def positions(self):
        return self.state["positions"]

    @property
    def used_capital(self):
        return self.pending + sum(p["reserve"] for p in self.positions.values())

    async def init(self):
        async with aiosqlite.connect(self.path) as d:
            await d.executescript(self.sql(SCHEMA) + LEDGER_SCHEMA)
            await d.execute(
                self.sql("INSERT OR IGNORE INTO cex_dex_paper_state VALUES(1,0,?)"),
                (json.dumps(self.state),),
            )
            await d.commit()
            async with d.execute(
                self.sql("SELECT version,payload FROM cex_dex_paper_state WHERE id=1")
            ) as c:
                self.version, payload = await c.fetchone()
            self.state = json.loads(payload)

    async def cycle(self, route=None, entry_enabled=True):
        async with self.lock:
            try:
                return await self._cycle(route, entry_enabled)
            finally:
                self.pending = 0

    async def request_exit(self, ident):
        """Persist a paired PAPER exit intent; the monitor still needs fresh quotes."""
        if not self.virtual or self.namespace != 'paired':
            raise ValueError('PAIRED_PAPER_ONLY')
        async with self.lock:
            state=copy.deepcopy(self.state)
            p=state['positions'].get(str(ident))
            if p is None:
                raise ValueError('POSITION_ALREADY_CLOSED')
            if p.get('operator_exit_requested') or p.get('exit_quote'):
                return
            p['operator_exit_requested']=self.clock()
            async with aiosqlite.connect(self.path) as db:
                await db.execute('BEGIN IMMEDIATE')
                c=await db.execute(self.sql('UPDATE cex_dex_paper_state SET version=version+1,payload=? WHERE id=1 AND version=?'),
                                   (json.dumps(state),self.version))
                if c.rowcount!=1:
                    raise ValueError('DEX_PAPER_CONCURRENT_WRITER')
                await db.execute(self.sql('UPDATE cex_dex_paper SET payload=? WHERE id=?'),(json.dumps(p),p['id']))
                await db.execute(self.sql('INSERT INTO cex_dex_paper_decisions(ts,reason,payload) VALUES(?,?,?)'),
                                 (self.clock(),'MANUAL_EXIT_REQUESTED',json.dumps(dict(position_id=p['id']))))
                await db.commit()
            self.state,self.version=state,self.version+1

    async def _cycle(self, route, entry_enabled):
        state = copy.deepcopy(self.state)
        saved, marks, events, closed, decisions, opened = [], [], [], [], [], []
        for key, p in list(state["positions"].items()):
            pending = p["status"] == "EXIT_ACCOUNTING_PENDING"
            until = p["exit_at"] if pending else self.clock()
            # Historical IO precedes the fresh actionable exit quotes.
            try:
                if getattr(self.source, 'policy_version', 1) == 2:
                    h = await asyncio.wait_for(self.source.settlements(p, until), 1)
                else:
                    h = await self.source.settlements(p, until)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                from .funding_paper_source import History
                h = History(False, 0, (), 'FUNDING_HISTORY_TIMEOUT' if isinstance(exc, asyncio.TimeoutError)
                            else 'FUNDING_HISTORY_UNAVAILABLE', p['opened_at'])
            try:
                x = p["exit_quote"] if pending else await self.source.exit(p)
            except asyncio.CancelledError:
                raise
            except Exception:
                x = dict(ok=False, reason='DEX_EXIT_UNAVAILABLE')
            if not x.get("ok"):
                p["data_reason"] = x.get("reason", "DEX_EXIT_UNAVAILABLE")
                p.setdefault('data_unavailable_since', self.clock())
                state['last_monitor_reason'] = p['data_reason']
                decisions.append((p['data_reason'], dict(stage='MONITOR', position_id=p['id'],
                    symbol=p['symbol'], data_unavailable_since=p['data_unavailable_since'],
                    funding_reason=h.reason, reserve_preserved=True,
                    diagnostics=x.get('diagnostics', {}))))
                saved.append(p)
                continue
            if not pending:
                h = self.source.extend_history(p, h, x["ts"])
            if x["ts"] < p.get("last_mark", {}).get("ts", p["opened_at"]):
                p["data_reason"] = "DEX_MARK_TIME_REGRESSION"
                saved.append(p)
                continue
            m = self.source.mark(p, x, h) if self.virtual else mark(p, x, h)
            marks.append((p["id"], m))
            p.update(
                last_mark=m, net=m["net"], funding_reason=h.reason, data_reason="OK"
            )
            p.pop('data_unavailable_since', None)
            state['last_monitor_reason'] = 'OK'
            if not h.verified:
                decisions.append((h.reason, dict(stage='FUNDING', position_id=p['id'],
                    symbol=p['symbol'], reserve_preserved=True)))
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
                            if h.verified and m["net"] >= (max(self.min_edge, p["entry_edge"] * 0.7) if self.virtual else p["entry_edge"] * 0.7)
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
            if not pending and p.get('reference_policy', {}).get('version') == 2:
                from .dex_reference_policy import exit_reason
                reason = exit_reason(p, m, h.verified)
            if not pending and p.get('paired_policy'):
                from .paired_paper_policy import exit_reason
                reason = exit_reason(p['paired_policy'], p['opened_at'], p['best_net'], m, h.verified)
            if not pending and self.virtual and self.namespace=='paired' and p.get('operator_exit_requested') is not None:
                reason = 'MANUAL_EXIT'
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
        if self.virtual and closed:
            state["closed_count"] = state.get("closed_count", 0) + len(closed)
            state["realized_net"] = state.get("realized_net", 0) + sum(p["net"] for p in closed)
            if self.namespace:
                for p in closed:
                    if getattr(self.source, 'policy_version', 1) == 2:
                        from .dex_reference_policy import record_close, record_version_stats
                        record_close(state, p)
                        record_version_stats(state, p)
                    key = json.dumps(p["virtual_route_key"], separators=(",", ":"))
                    if p["net"] < 0:
                        state.setdefault("route_cooldowns", {})[key] = p["closed_at"] + 600
                    state["wins"] = state.get("wins", 0) + int(p["net"] > 0)
                    state["net_peak"] = max(state.get("net_peak", 0), state["realized_net"])
                    state["closed_drawdown"] = max(state.get("closed_drawdown", 0), state["net_peak"] - state["realized_net"])
                    day = int(p["closed_at"] // 86400)
                    if state.get("day") != day:
                        state.update(day=day, day_net=0)
                    state["day_net"] += p["net"]
        if (
            route is not None
            and entry_enabled
            and not state["positions"]
            and not closed
        ):
            reason = "SHARED_CAPITAL_LOW"
            reservation = self.source.reserve_for_route(route) if self.virtual else 12
            decision_detail = {}
            failure_detail = {}
            if self.used_capital + self.external_reserved() + reservation <= self.budget():
                # Make the reservation visible to the other Paper modules during IO.
                self.pending = reservation
                x = await self.source.entry(route)
                if self.virtual:
                    failure_detail = x.get("diagnostics", {})
                reason = x.get("reason", "DEX_ENTRY_UNAVAILABLE")
                if x.get("ok"):
                    p = x["position"]
                    if self.virtual:
                        decision_detail = {k: p[k] for k in ("virtual_mode", "cex_venue", "cex_side", "entry_price", "entry_edge", "entry_fees", "fee_source", "dex_reference_bid", "dex_reference_ask", "reserve")}
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
                            id=pid, closed_at=None, status="OPEN",
                            net=p.get('entry_immediate_net', 0), best_net=0
                        )
                        state["positions"][str(pid)] = p
                        if self.namespace and p.get("channel_signal"):
                            key = json.dumps(p["virtual_route_key"], separators=(",", ":"))
                            state.setdefault("consumed_signals", {})[key] = p["channel_signal"]["timestamp"]
                        saved.append(p)
                        opened.append(copy.deepcopy(p))
                        reason = "DEX_PAPER_OPEN"
            # Never persist the route: it contains the wallet address.
            decisions.append(
                (
                    reason,
                    dict(
                        label=route.get("label", "DEX"),
                        mode="DEX_VIRTUAL_INDICATIVE_MODEL" if self.virtual else "DEX_FIRM_PUBLIC_HISTORY_MODEL",
                        **decision_detail,
                        **({"diagnostics": failure_detail} if self.virtual and failure_detail else {}),
                    ),
                )
            )
            if self.namespace and failure_detail.get("signal_timestamp"):
                history = state.setdefault("signal_checks", [])
                history.append(dict(ts=self.clock(), symbol=route.get("cex_symbol"), reason=reason, diagnostics=failure_detail))
                del history[:-12]
            state["last_decision"] = reason
            if self.virtual:
                state["last_detail"] = decision_detail
                state["last_failure_detail"] = failure_detail
                state["last_route"] = dict(label=route.get("label", route.get("cex_symbol", "DEX")),
                                          symbol=route.get("cex_symbol"), venue=route.get("cex_venue"), chain=route.get("chain_id"), ts=self.clock())
                state["checked_count"] = state.get("checked_count", 0) + 1
                counts = state.setdefault("decision_counts", {})
                counts[reason] = counts.get(reason, 0) + 1
        async with aiosqlite.connect(self.path) as d:
            await d.execute("BEGIN IMMEDIATE")
            cursor = await d.execute(
                self.sql("UPDATE cex_dex_paper_state SET version=version+1,payload=? WHERE id=1 AND version=?"),
                (json.dumps(state), self.version),
            )
            if cursor.rowcount != 1:
                raise ValueError("DEX_PAPER_CONCURRENT_WRITER")
            for p in saved:
                await d.execute(
                    self.sql("INSERT INTO cex_dex_paper VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET closed_at=excluded.closed_at,status=excluded.status,net=excluded.net,payload=excluded.payload"),
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
                    self.sql("INSERT INTO cex_dex_paper_marks(position_id,ts,net,payload) VALUES(?,?,?,?)"),
                    (pid, m["ts"], m["net"], json.dumps(m)),
                )
            for pid, e in events:
                async with d.execute(
                    self.sql("SELECT payload FROM cex_dex_paper_events WHERE position_id=? AND venue=? AND ts=?"),
                    (pid, e["venue"], e["ts"]),
                ) as c:
                    old = await c.fetchone()
                if old and json.loads(old[0]) != e:
                    raise ValueError("DEX_FUNDING_MODEL_EVENT_CONFLICT")
                await d.execute(
                    self.sql("INSERT OR IGNORE INTO cex_dex_paper_events VALUES(?,?,?,?,?)"),
                    (pid, e["venue"], e["ts"], e["amount"], json.dumps(e)),
                )
            for reason, row in decisions:
                await d.execute(
                    self.sql("INSERT INTO cex_dex_paper_decisions(ts,reason,payload) VALUES(?,?,?)"),
                    (self.clock(), reason, json.dumps(row)),
                )
            for p in closed:
                await d.execute(
                    "INSERT INTO ledger VALUES(?,?,?,?,?,?)",
                    (
                        p["closed_at"],
                        "CEX_DEX_" + self.namespace.upper() + "_EXPERIMENT_NET" if self.namespace else ("CEX_DEX_VIRTUAL_PAPER_NET" if self.virtual else "CEX_DEX_PAPER_NET"),
                        ("dex-" + self.namespace + ":" if self.namespace else ("dex-virtual:" if self.virtual else "dex:")) + str(p["id"]),
                        p["cex_venue"],
                        p["net"],
                        p.get("model", "DEX_FIRM_PUBLIC_HISTORY_MODEL"),
                    ),
                )
            await d.commit()
        self.state, self.version = state, self.version + 1
        self.pending = 0
        if self.on_closed:
            for p in closed:
                await self.on_closed(p)
        if self.on_opened:
            for p in opened:
                await self.on_opened(p)
        return closed


class Cycle:
    def __init__(self, paper, routes):
        self.paper, self.routes, self.index, self.entry_enabled = paper, routes, 0, True
        self.scan_interval = 5 if getattr(paper.source,'policy_version',1)==2 else 30

    @property
    def interval(self):
        return 1 if self.paper.positions and getattr(self.paper.source,'policy_version',1)==2 else self.scan_interval

    @interval.setter
    def interval(self, value):
        self.scan_interval = value

    async def cycle(self):
        route = self.routes[self.index % len(self.routes)] if self.routes else None
        from .dex_signal_focus import enabled, entry_venue_allowed
        allowed_routes = [r for r in self.routes if (self.paper.source.venue_allowed(r["cex_venue"]) if hasattr(self.paper.source, "venue_allowed") else entry_venue_allowed(r["cex_venue"]))]
        route = allowed_routes[self.index % len(allowed_routes)] if allowed_routes else None
        signals = getattr(self.paper.source, "signals", None)
        if getattr(self.paper.source, "signals_only", enabled("DEX_CHANNEL_SIGNALS_ONLY")) and signals:
            active = [r for r in allowed_routes if signals.get(r)[0] is not None]
            if active:
                route = active[self.index % len(active)]
        elif getattr(self.paper.source, "focus", False):
            from .dex_virtual_paper_source import route_key
            confirmations = getattr(self.paper.source, "confirmations", {})
            pending = [r for r in allowed_routes if route_key(r) in confirmations and 2 <= self.paper.clock() - confirmations[route_key(r)][0] <= 60]
            if pending:
                route = pending[0]
        self.index += 1
        await self.paper.cycle(None if self.paper.positions else route, self.entry_enabled)
        rows = [
            dict(
                strategy="dex_" + self.paper.namespace if self.paper.namespace else "cex_dex",
                symbol=p["symbol"],
                ts=self.paper.clock(),
                paper_allowed=True,
                live_allowed=False,
                evidence_mode="DEX_VIRTUAL_INDICATIVE_MODEL" if self.paper.virtual else "DEX_FIRM_PUBLIC_HISTORY_MODEL",
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
                    evidence_mode="DEX_VIRTUAL_INDICATIVE_MODEL" if self.paper.virtual else "DEX_FIRM_PUBLIC_HISTORY_MODEL",
                    paper_allowed=False,
                    live_allowed=False,
                )
            )
        return rows
