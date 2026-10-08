"""Durable funding Paper. Public history produces modeled cashflow, never account PnL."""

import asyncio, copy, json, math, time
import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS funding_paper_state(id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER,payload TEXT);
CREATE TABLE IF NOT EXISTS funding_paper(id INTEGER PRIMARY KEY,symbol TEXT,buy TEXT,sell TEXT,opened_at REAL,closed_at REAL,status TEXT,net REAL,payload TEXT);
CREATE TABLE IF NOT EXISTS funding_paper_marks(id INTEGER PRIMARY KEY,position_id INTEGER,ts REAL,net REAL,payload TEXT);
CREATE TABLE IF NOT EXISTS funding_paper_events(position_id INTEGER,venue TEXT,ts REAL,amount REAL,payload TEXT,PRIMARY KEY(position_id,venue,ts));
CREATE TABLE IF NOT EXISTS funding_paper_decisions(id INTEGER PRIMARY KEY,ts REAL,symbol TEXT,reason TEXT,payload TEXT);
"""


class Engine:
    def __init__(
        self,
        path,
        source,
        capital=50,
        max_seconds=28800,
        min_carry=0.03,
        max_basis_loss=0.02,
        clock=time.time,
    ):
        if (
            not all(
                math.isfinite(float(x))
                for x in (capital, max_seconds, min_carry, max_basis_loss)
            )
            or capital <= 0
            or max_seconds <= 0
            or min_carry < 0
            or not 0 < max_basis_loss < 1
        ):
            raise ValueError("FUNDING_PAPER_CONFIG_INVALID")
        self.path = str(path)
        self.source = source
        self.capital = capital
        self.max_seconds = max_seconds
        self.min_carry = min_carry
        self.max_basis_loss = max_basis_loss
        self.clock = clock
        self.budget = lambda: self.capital
        self.state = {"positions": {}, "next_id": 1}
        self.version = 0
        self.pending = 0
        self.lock = asyncio.Lock()
        self.external_reserved = lambda: 0
        self.allow_open = lambda x: True
        self.on_closed = None

    @property
    def positions(self):
        return self.state["positions"]

    @property
    def used_capital(self):
        return self.pending + sum(
            p["base_qty"] * (p["entry_buy"] + p["entry_sell"])
            + p["entry_fees"]
            + p["safety"]
            for p in self.positions.values()
        )

    async def init(self):
        async with aiosqlite.connect(self.path) as d:
            await d.executescript(SCHEMA)
            await d.execute(
                "INSERT OR IGNORE INTO funding_paper_state VALUES(1,0,?)",
                (json.dumps(self.state),),
            )
            await d.commit()
            async with d.execute(
                "SELECT version,payload FROM funding_paper_state WHERE id=1"
            ) as c:
                self.version, payload = await c.fetchone()
        self.state = json.loads(payload)

    def _mark(self, p, x, h):
        qty = p["base_qty"]
        fees = qty * (x["exit_buy"] + x["exit_sell"]) * x["fee_pct"] / 400
        gross = qty * (
            (x["exit_buy"] - p["entry_buy"]) + (p["entry_sell"] - x["exit_sell"])
        )
        estimate = gross - p["entry_fees"] - fees - p["safety"] + h.amount
        return dict(
            ts=x["ts"],
            base_qty=qty,
            gross=gross,
            entry_fees=p["entry_fees"],
            exit_fees=fees,
            safety=p["safety"],
            funding=h.amount,
            net=estimate,
            funding_known=h.verified,
            mode="FUNDING_PUBLIC_HISTORY_MODEL",
        )

    async def cycle(self, candidates, entry_enabled=True):
        async with self.lock:
            state = copy.deepcopy(self.state)
            saved = []
            marks = []
            events = []
            decisions = []
            closed = []
            closed_symbols = set()
            for key, p in list(state["positions"].items()):
                if p["status"] == "EXIT_ACCOUNTING_PENDING":
                    x = p["exit_quote"]
                    h = await self.source.settlements(p, p["exit_at"])
                    if not h.verified or h.covered_until + 1e-6 < p["exit_at"]:
                        p["funding_reason"] = h.reason
                        saved.append(p)
                        continue
                    mark = self._mark(p, x, h)
                    mark["ts"] = p["exit_at"]
                    marks.append((p["id"], mark))
                    events.extend((p["id"], e) for e in h.events)
                    p.update(
                        status="CLOSED",
                        net=mark["net"],
                        last_mark=mark,
                        closed_at=p["exit_at"],
                        funding_reason=h.reason,
                    )
                    del state["positions"][key]
                    saved.append(p)
                    closed.append(copy.deepcopy(p))
                    closed_symbols.add(p["symbol"])
                    continue
                x = await self.source.quote(
                    p["symbol"], p["buy"], p["sell"], p["base_qty"]
                )
                if not x.get("ok"):
                    p["data_reason"] = x.get("reason", "MARKET_UNAVAILABLE")
                    saved.append(p)
                    continue
                if x["ts"] < p.get("last_mark", {}).get("ts", p["opened_at"]):
                    continue
                h = await self.source.settlements(p, x["ts"])
                if self.clock() - x["ts"] > getattr(self.source, "max_age", 1.5):
                    p["data_reason"] = "BOOK_STALE_AFTER_HISTORY"
                    saved.append(p)
                    continue
                mark = self._mark(p, x, h)
                marks.append((p["id"], mark))
                events.extend((p["id"], e) for e in h.events)
                p.update(
                    last_mark=mark,
                    net=mark["net"],
                    funding_reason=h.reason,
                    data_reason="OK",
                )
                # Forecast rates are excluded. Negative basis alone triggers protective exit.
                reason = (
                    "TIME_STOP"
                    if x["ts"] - p["opened_at"] >= self.max_seconds
                    else (
                        "BASIS_LOSS_STOP"
                        if mark["gross"]
                        <= -p["base_qty"] * p["entry_buy"] * self.max_basis_loss
                        else None
                    )
                )
                if reason:
                    p.update(
                        status="EXIT_ACCOUNTING_PENDING",
                        exit_at=x["ts"],
                        exit_quote=x,
                        exit_reason=reason,
                    )
                    closed_symbols.add(p["symbol"])
                saved.append(p)
            for row in candidates:
                if not entry_enabled:
                    continue
                symbol = row["symbol"]
                reason = "POSITION_CAPACITY"
                if not state["positions"] and symbol not in closed_symbols:
                    x = await self.source.quote(
                        symbol, row["long_venue"], row["short_venue"]
                    )
                    reason = x.get("reason", "MARKET_UNAVAILABLE")
                    if x.get("ok"):
                        qty = x["base_qty"]
                        required = qty * (x["entry_buy"] + x["entry_sell"])
                        now = x["ts"]
                        periods = []
                        for prefix in ("long", "short"):
                            first = x[prefix + "_next"]
                            interval = x[prefix + "_interval"] * 3600
                            periods.append(
                                0
                                if first > now + self.max_seconds
                                else 1
                                + int((now + self.max_seconds - first) // interval)
                            )
                        carry = (
                            x["short_rate"] * periods[1] - x["long_rate"] * periods[0]
                        ) * 100
                        forecast = carry - x["fee_pct"] - x["safety_pct"]
                        if min(x["long_next"], x["short_next"]) <= now:
                            reason = "FUNDING_CALENDAR_STALE"
                        elif forecast < self.min_carry:
                            reason = "PROJECTED_CARRY_LOW"
                        elif not self.allow_open(x):
                            reason = "RISK_OR_VENUE_DISABLED"
                        elif (
                            required * (1 + x["fee_pct"] / 400)
                            + qty * x["entry_buy"] * x["safety_pct"] / 100
                            + self.external_reserved()
                            > self.budget()
                        ):
                            reason = "SHARED_CAPITAL_LOW"
                        else:
                            pid = state["next_id"]
                            state["next_id"] += 1
                            p = {k: v for k, v in x.items() if k != "ok"}
                            p.update(
                                id=pid,
                                opened_at=now,
                                closed_at=None,
                                status="OPEN",
                                net=0,
                                entry_fees=required * x["fee_pct"] / 400,
                                entry_fee_pct=x["fee_pct"],
                                safety=qty * x["entry_buy"] * x["safety_pct"] / 100,
                                projected_carry_pct=carry,
                            )
                            state["positions"][str(pid)] = p
                            saved.append(p)
                            reason = "FUNDING_PAPER_OPEN"
                decisions.append((symbol, reason, row))
            self.pending = max(
                0,
                sum(
                    p["base_qty"] * (p["entry_buy"] + p["entry_sell"])
                    + p["entry_fees"]
                    + p["safety"]
                    for p in state["positions"].values()
                )
                - self.used_capital,
            )
            try:
                async with aiosqlite.connect(self.path) as d:
                    await d.execute("BEGIN IMMEDIATE")
                    cursor = await d.execute(
                        "UPDATE funding_paper_state SET version=version+1,payload=? WHERE id=1 AND version=?",
                        (json.dumps(state), self.version),
                    )
                    if cursor.rowcount != 1:
                        raise ValueError("FUNDING_PAPER_CONCURRENT_WRITER")
                    for p in saved:
                        await d.execute(
                            "INSERT INTO funding_paper VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET closed_at=excluded.closed_at,status=excluded.status,net=excluded.net,payload=excluded.payload",
                            (
                                p["id"],
                                p["symbol"],
                                p["buy"],
                                p["sell"],
                                p["opened_at"],
                                p["closed_at"],
                                p["status"],
                                p["net"],
                                json.dumps(p),
                            ),
                        )
                    for pid, m in marks:
                        await d.execute(
                            "INSERT INTO funding_paper_marks(position_id,ts,net,payload) VALUES(?,?,?,?)",
                            (pid, m["ts"], m["net"], json.dumps(m)),
                        )
                    for pid, e in events:
                        async with d.execute(
                            "SELECT amount FROM funding_paper_events WHERE position_id=? AND venue=? AND ts=?",
                            (pid, e["venue"], e["ts"]),
                        ) as c:
                            old = await c.fetchone()
                        if old and not math.isclose(old[0], e["amount"], abs_tol=1e-10):
                            raise ValueError("FUNDING_MODEL_EVENT_CONFLICT")
                        await d.execute(
                            "INSERT OR IGNORE INTO funding_paper_events VALUES(?,?,?,?,?)",
                            (pid, e["venue"], e["ts"], e["amount"], json.dumps(e)),
                        )
                    for symbol, reason, row in decisions:
                        await d.execute(
                            "INSERT INTO funding_paper_decisions(ts,symbol,reason,payload) VALUES(?,?,?,?)",
                            (self.clock(), symbol, reason, json.dumps(row)),
                        )
                    await d.commit()
                self.state = state
                self.version += 1
            finally:
                self.pending = 0
            if self.on_closed:
                for p in closed:
                    await self.on_closed(p)
            return closed
