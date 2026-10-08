"""Inventory-backed, atomic Paper round trips. No borrowing or transfer simulation."""

import asyncio, copy, json, math, time
import aiosqlite
from .spot_future_exit import decide

SCHEMA = """
CREATE TABLE IF NOT EXISTS spot_spot_state(id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS spot_spot_paper(id INTEGER PRIMARY KEY,symbol TEXT,buy TEXT,sell TEXT,opened_at REAL,closed_at REAL,status TEXT,net REAL,payload TEXT);
CREATE TABLE IF NOT EXISTS spot_spot_marks(id INTEGER PRIMARY KEY,position_id INTEGER,ts REAL,net REAL,payload TEXT);
CREATE TABLE IF NOT EXISTS spot_spot_decisions(id INTEGER PRIMARY KEY,ts REAL,symbol TEXT,buy TEXT,sell TEXT,reason TEXT,payload TEXT);
"""


def number(value, positive=False):
    x = float(value)
    if not math.isfinite(x) or x < 0 or (positive and x == 0):
        raise ValueError("INVALID_INVENTORY_VALUE")
    return x


def seed_state(seed):
    balances = {}
    value = 0
    for venue, row in seed.items():
        quote = number(row.get("USDT", 0))
        assets = row.get("assets", {})
        account = {"USDT": quote}
        value += quote
        for asset, item in assets.items():
            if asset == "USDT":
                raise ValueError("ASSET_IDENTITY_INVALID")
            qty = number(item["qty"])
            price = number(item["price"], True)
            account[asset] = qty
            value += qty * price
        balances[venue] = account
    return dict(
        balances=balances,
        initial_value=value,
        realized=0,
        positions={},
        next_id=1,
        seed=seed,
    )


class Engine:
    def __init__(
        self,
        path,
        seed=None,
        capital=50,
        entry_edge=1,
        max_age=600,
        trailing=0.2,
        clock=time.time,
    ):
        self.path = str(path)
        self.seed = seed or {}
        self.capital = capital
        self.edge = entry_edge
        self.max_age = max_age
        self.trailing = trailing
        self.clock = clock
        self.external_reserved = lambda: 0
        self.allow_open = lambda x: True
        self.on_closed = None
        self.state = None
        self.version = 0
        self.lock = asyncio.Lock()

    @property
    def positions(self):
        return (self.state or {}).get("positions", {})

    @property
    def allocated_capital(self):
        return (self.state or {}).get("initial_value", 0)

    @property
    def used_capital(self):
        return (self.state or {}).get("initial_value", 0) + max(
            0, (self.state or {}).get("realized", 0)
        )

    async def init(self):
        initial = seed_state(self.seed)
        if initial["initial_value"] > self.capital:
            raise ValueError("SPOT_INVENTORY_EXCEEDS_CAPITAL")
        async with aiosqlite.connect(self.path) as d:
            await d.executescript(SCHEMA)
            await d.execute("BEGIN IMMEDIATE")
            await d.execute(
                "INSERT OR IGNORE INTO spot_spot_state VALUES(1,0,?)",
                (json.dumps(initial),),
            )
            async with d.execute(
                "SELECT version,payload FROM spot_spot_state WHERE id=1"
            ) as c:
                version, payload = await c.fetchone()
            state = json.loads(payload)
            if self.seed and state["seed"] != self.seed:
                if (
                    state["initial_value"] == 0
                    and not state["balances"]
                    and not state["positions"]
                    and state["realized"] == 0
                ):
                    state = initial
                    version += 1
                    await d.execute(
                        "UPDATE spot_spot_state SET version=?,payload=? WHERE id=1",
                        (version, json.dumps(state)),
                    )
                else:
                    raise ValueError("PERSISTED_INVENTORY_SEED_CONFLICT")
            if state["initial_value"] > self.capital:
                raise ValueError("PERSISTED_INVENTORY_EXCEEDS_CAPITAL")
            await d.commit()
        self.state = state
        self.version = version

    def watch_routes(self):
        return list(self.positions.values())

    def _valid(self, x):
        if x["buy"] == x["sell"] or not x["symbol"].endswith("/USDT"):
            raise ValueError("SPOT_ROUTE_INVALID")
        for k in ("base_qty", "entry_buy", "entry_sell", "exit_buy", "exit_sell"):
            number(x[k], True)
        for k in ("fee_pct", "safety_pct"):
            number(x[k])
        expected = (
            (x["entry_sell"] - x["entry_buy"]) / x["entry_buy"] * 100
            - x["fee_pct"]
            - x["safety_pct"]
        )
        if not math.isclose(float(x["net"]), expected, abs_tol=1e-8):
            raise ValueError("SPOT_EDGE_ACCOUNTING_MISMATCH")
        if not math.isfinite(float(x["net"])):
            raise ValueError("SPOT_EDGE_INVALID")
        if x["fee_pct"] >= 400:
            raise ValueError("SPOT_FEE_INVALID")
        stamp = float(x["ts"])
        if not math.isfinite(stamp) or not 0 <= self.clock() - stamp <= 12:
            raise ValueError("SPOT_QUOTE_STALE")

    async def cycle(self, rows, entry_enabled=True):
        async with self.lock:
            state = copy.deepcopy(self.state)
            marks = []
            saved = []
            closed = []
            decisions = []
            closed_routes = set()
            for x in rows:
                try:
                    self._valid(x)
                except (ValueError, KeyError, TypeError) as error:
                    rejected = dict(
                        x,
                        ts=self.clock(),
                        symbol=str(x.get("symbol", "")),
                        buy=str(x.get("buy", "")),
                        sell=str(x.get("sell", "")),
                    )
                    decisions.append((rejected, "QUOTE_UNVERIFIED:" + str(error)))
                    continue
                route = (x["symbol"], x["buy"], x["sell"])
                qty = x["base_qty"]
                asset = x["symbol"].split("/")[0]
                for key, p in list(state["positions"].items()):
                    if (p["symbol"], p["buy"], p["sell"]) != route:
                        continue
                    if not math.isclose(qty, p["base_qty"], rel_tol=1e-8):
                        continue
                    if x["ts"] < max(
                        p["opened_at"], p.get("last_mark", {}).get("ts", p["opened_at"])
                    ):
                        continue
                    rate = x["fee_pct"] / 400
                    exit_fees = qty * (x["exit_buy"] + x["exit_sell"]) * rate
                    gross = qty * (
                        (p["entry_sell"] - p["entry_buy"])
                        + (x["exit_buy"] - x["exit_sell"])
                    )
                    net = gross - p["entry_fees"] - exit_fees - p["safety"]
                    p["best_net"] = max(p["best_net"], net)
                    p["net"] = net
                    mark = dict(
                        ts=x["ts"],
                        net=net,
                        gross=gross,
                        entry_fees=p["entry_fees"],
                        exit_fees=exit_fees,
                        safety=p["safety"],
                        funding=0,
                        base_qty=qty,
                        mode="PAPER_MODEL",
                        exit_buy=x["exit_buy"],
                        exit_sell=x["exit_sell"],
                    )
                    p["last_mark"] = mark
                    marks.append((p["id"], mark))
                    result = decide(
                        net,
                        p["best_net"],
                        x["ts"] - p["opened_at"],
                        self.max_age,
                        self.trailing,
                    )
                    if result.close:
                        buy = state["balances"][p["buy"]]
                        sell = state["balances"][p["sell"]]
                        restore_cost = qty * x["exit_sell"] * (1 + rate)
                        if (
                            buy.get(asset, 0) + 1e-10 < qty
                            or sell.get("USDT", 0) + 1e-10 < restore_cost
                        ):
                            p["exit_blocked"] = "EXIT_INVENTORY_LOW"
                            decisions.append((x, "EXIT_INVENTORY_LOW"))
                            saved.append(p)
                            continue
                        buy[asset] -= qty
                        buy["USDT"] += qty * x["exit_buy"] * (1 - rate)
                        sell["USDT"] -= restore_cost
                        sell[asset] = sell.get(asset, 0) + qty
                        p.update(status=result.reason, closed_at=x["ts"])
                        state["realized"] += net
                        del state["positions"][key]
                        closed.append(copy.deepcopy(p))
                        closed_routes.add(route)
                    saved.append(copy.deepcopy(p))
                reason = "ENTRY_DISABLED"
                if (
                    entry_enabled
                    and not x.get("watch_only")
                    and route not in closed_routes
                ):
                    rate = x["fee_pct"] / 400
                    required = (
                        qty * x["entry_buy"] * (1 + rate)
                        + qty * x["entry_buy"] * x["safety_pct"] / 100
                    )
                    buy = state["balances"].get(x["buy"], {})
                    sell = state["balances"].get(x["sell"], {})
                    if state["positions"]:
                        reason = "POSITION_CAPACITY"
                    elif (
                        state["initial_value"]
                        + max(0, state["realized"])
                        + self.external_reserved()
                        > self.capital
                    ):
                        reason = "SHARED_CAPITAL_LOW"
                    elif not self.allow_open(x):
                        reason = "RISK_OR_VENUE_DISABLED"
                    elif x["net"] < self.edge:
                        reason = "BELOW_ENTRY_EDGE"
                    elif buy.get("USDT", 0) + 1e-10 < required:
                        reason = "BUY_QUOTE_LOW"
                    elif sell.get(asset, 0) + 1e-10 < qty:
                        reason = "SELL_BASE_LOW"
                    else:
                        reason = "PAPER_OPEN"
                        pid = state["next_id"]
                        state["next_id"] += 1
                        buy["USDT"] -= required
                        buy[asset] = buy.get(asset, 0) + qty
                        sell[asset] -= qty
                        sell["USDT"] = sell.get("USDT", 0) + qty * x["entry_sell"] * (
                            1 - rate
                        )
                        p = {
                            k: x[k]
                            for k in (
                                "symbol",
                                "buy",
                                "sell",
                                "base_qty",
                                "entry_buy",
                                "entry_sell",
                            )
                        }
                        p.update(
                            id=pid,
                            opened_at=x["ts"],
                            closed_at=None,
                            status="OPEN",
                            net=0,
                            best_net=0,
                            entry_fees=qty * (x["entry_buy"] + x["entry_sell"]) * rate,
                            safety=qty * x["entry_buy"] * x["safety_pct"] / 100,
                            entry_fee_pct=x["fee_pct"],
                        )
                        # The entry mark records the observed immediate round-trip cost.
                        ef = qty * (x["exit_buy"] + x["exit_sell"]) * rate
                        g = qty * (
                            (x["entry_sell"] - x["entry_buy"])
                            + (x["exit_buy"] - x["exit_sell"])
                        )
                        p["net"] = g - p["entry_fees"] - ef - p["safety"]
                        p["last_mark"] = dict(
                            ts=x["ts"],
                            base_qty=qty,
                            gross=g,
                            entry_fees=p["entry_fees"],
                            exit_fees=ef,
                            safety=p["safety"],
                            funding=0,
                            net=p["net"],
                            mode="PAPER_MODEL",
                            exit_buy=x["exit_buy"],
                            exit_sell=x["exit_sell"],
                        )
                        state["positions"][str(pid)] = p
                        saved.append(copy.deepcopy(p))
                        marks.append((pid, p["last_mark"]))
                decisions.append((x, reason))
            async with aiosqlite.connect(self.path) as d:
                await d.execute("BEGIN IMMEDIATE")
                cursor = await d.execute(
                    "UPDATE spot_spot_state SET version=version+1,payload=? WHERE id=1 AND version=?",
                    (json.dumps(state), self.version),
                )
                if cursor.rowcount != 1:
                    raise ValueError("SPOT_INVENTORY_CONCURRENT_WRITER")
                for p in saved:
                    await d.execute(
                        "INSERT INTO spot_spot_paper VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET closed_at=excluded.closed_at,status=excluded.status,net=excluded.net,payload=excluded.payload",
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
                        "INSERT INTO spot_spot_marks(position_id,ts,net,payload) VALUES(?,?,?,?)",
                        (pid, m["ts"], m["net"], json.dumps(m)),
                    )
                for x, reason in decisions:
                    await d.execute(
                        "INSERT INTO spot_spot_decisions(ts,symbol,buy,sell,reason,payload) VALUES(?,?,?,?,?,?)",
                        (
                            x["ts"],
                            x["symbol"],
                            x["buy"],
                            x["sell"],
                            reason,
                            json.dumps(x),
                        ),
                    )
                await d.commit()
            self.state = state
            self.version += 1
            if self.on_closed:
                for p in closed:
                    await self.on_closed(p)
            return closed
