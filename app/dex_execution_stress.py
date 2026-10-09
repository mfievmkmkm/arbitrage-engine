"""Offline sequential DEX-then-CEX stress. Model assumptions, no trading authority.

DEX is atomic: success at quote bounds, known revert (gas), or unknown. A quote
does not prove execution. Public books/quotes may only be used after receipt.
Missing data and residual inventory return net=None, never fabricated flat.
Funding is excluded; results never enter the bankroll or LIVE ledger.
"""

import bisect
import json
import math
import time
from dataclasses import dataclass, asdict
from decimal import Decimal
from html import escape
import aiosqlite
from .dex_firm_simulation import integer
from .execution_sim import walk
from .recovery_market import levels

SCHEMA = """
CREATE TABLE IF NOT EXISTS dex_quote_history(id INTEGER PRIMARY KEY,chain_id INTEGER,sell_token TEXT,buy_token TEXT,observed_at REAL,payload TEXT);
CREATE INDEX IF NOT EXISTS idx_dex_quote_time ON dex_quote_history(chain_id,sell_token,buy_token,observed_at);
CREATE TABLE IF NOT EXISTS dex_stress_runs(id INTEGER PRIMARY KEY,created_at REAL,payload TEXT);
CREATE TABLE IF NOT EXISTS dex_stress_results(id INTEGER PRIMARY KEY,run_id INTEGER,position_id INTEGER,scenario TEXT,status TEXT,net REAL,payload TEXT);
"""


class History:
    def __init__(self, path, clock=time.time, max_rows=100000, retention=259200):
        if (
            type(max_rows) is not int
            or max_rows < 1
            or not math.isfinite(retention)
            or retention <= 0
        ):
            raise ValueError("DEX_HISTORY_CONFIG_INVALID")
        self.path, self.clock, self.max_rows, self.retention = (
            str(path),
            clock,
            max_rows,
            retention,
        )

    async def init(self):
        async with aiosqlite.connect(self.path) as d:
            await d.executescript(SCHEMA)
            await d.commit()

    async def record(self, quote, gas, gas_book, native_decimals=18):
        observed = self.clock()
        q = dict(quote)
        if (
            q.get("simulation_verified") is not True
            or q.get("ok") is not True
            or not q["ts"] <= q["received_at"] <= observed
            or observed - q["ts"] > 15
            or not math.isfinite(gas)
            or gas < 0
        ):
            raise ValueError("DEX_HISTORY_PROOF_INVALID")
        if type(native_decimals) is not int or not 0 <= native_decimals <= 36:
            raise ValueError("DEX_HISTORY_GAS_DECIMALS_INVALID")
        from .public_books import normalize

        normalize(gas_book, gas_book["symbol"], gas_book["requested_at"], observed, 1.5)
        calculated = float(
            Decimal(integer(q["network_fee_raw"], "DEX_HISTORY_GAS"))
            / Decimal(10**native_decimals)
            * Decimal(str(gas_book["asks"][0][0]))
        )
        if not math.isclose(calculated, gas, rel_tol=1e-10, abs_tol=1e-10):
            raise ValueError("DEX_HISTORY_GAS_LINEAGE_INVALID")
        payload = dict(
            quote=q,
            gas=gas,
            gas_book=gas_book,
            observed_at=observed,
            mode="FIRM_QUOTE_NOT_EXECUTED",
            native_decimals=native_decimals,
        )
        async with aiosqlite.connect(self.path) as d:
            await d.execute(
                "INSERT INTO dex_quote_history(chain_id,sell_token,buy_token,observed_at,payload) VALUES(?,?,?,?,?)",
                (
                    q["chain_id"],
                    q["sell_token"],
                    q["buy_token"],
                    observed,
                    json.dumps(payload),
                ),
            )
            await d.execute(
                "DELETE FROM dex_quote_history WHERE observed_at<?",
                (observed - self.retention,),
            )
            await d.execute(
                "DELETE FROM dex_quote_history WHERE id NOT IN (SELECT id FROM dex_quote_history ORDER BY id DESC LIMIT ?)",
                (self.max_rows,),
            )
            await d.commit()


@dataclass(frozen=True)
class Scenario:
    name: str
    dex_confirmation: float = 0
    cex_latency: float = 0
    recovery_latency: float = 0.5
    max_age: float = 1.5
    entry_outcome: str = "success"
    exit_outcome: str = "success"

    def __post_init__(self):
        if (
            any(
                type(v) not in (int, float) or not math.isfinite(v) or v < 0
                for v in (
                    self.dex_confirmation,
                    self.cex_latency,
                    self.recovery_latency,
                )
            )
            or not math.isfinite(self.max_age)
            or self.max_age <= 0
            or self.entry_outcome not in ("success", "revert", "unknown")
            or self.exit_outcome not in ("success", "revert", "unknown")
        ):
            raise ValueError("DEX_STRESS_SCENARIO_INVALID")


SCENARIOS = (
    Scenario("BOUND_BASE"),
    Scenario("DEX_2S_CEX_0_5S", 2, 0.5),
    Scenario("DEX_15S_CEX_1S", 15, 1),
    Scenario("ENTRY_REVERT", entry_outcome="revert"),
    Scenario("EXIT_REVERT", exit_outcome="revert"),
    Scenario("UNKNOWN_RECEIPT", entry_outcome="unknown"),
)


class Tape:
    def __init__(self, quotes, books):
        self.quotes = sorted(quotes, key=lambda q: q["observed_at"])
        self.books = sorted(
            (
                {
                    k: b[k]
                    for k in (
                        "venue",
                        "symbol",
                        "book_ts",
                        "received_at",
                        "contract_size",
                        "bids",
                        "asks",
                    )
                }
                for b in books
            ),
            key=lambda b: b["received_at"],
        )
        self.quote_times = [q["observed_at"] for q in self.quotes]
        self.book_times = [b["received_at"] for b in self.books]
        for row in self.quotes:
            q = row["quote"]
            if (
                q.get("simulation_verified") is not True
                or q.get("ok") is not True
                or not all(
                    math.isfinite(float(t))
                    for t in (q["ts"], q["received_at"], row["observed_at"], row["gas"])
                )
                or not q["ts"] <= q["received_at"] <= row["observed_at"]
                or row["gas"] < 0
            ):
                raise ValueError("DEX_STRESS_QUOTE_INVALID")
            if (
                type(row.get("native_decimals")) is not int
                or not 0 <= row["native_decimals"] <= 36
            ):
                raise ValueError("DEX_STRESS_GAS_DECIMALS_INVALID")
            b = row["gas_book"]
            from .public_books import normalize

            normalize(b, b["symbol"], b["requested_at"], row["observed_at"], 1.5)
            calculated = float(
                Decimal(integer(q["network_fee_raw"], "DEX_STRESS_GAS"))
                / Decimal(10 ** row["native_decimals"])
                * Decimal(str(b["asks"][0][0]))
            )
            if not math.isclose(calculated, row["gas"], rel_tol=1e-10, abs_tol=1e-10):
                raise ValueError("DEX_STRESS_GAS_LINEAGE_INVALID")
        for b in self.books:
            if (
                not all(
                    math.isfinite(float(b[k]))
                    for k in ("received_at", "book_ts", "contract_size")
                )
                or b["contract_size"] <= 0
                or b["book_ts"] > b["received_at"]
            ):
                raise ValueError("DEX_STRESS_BOOK_INVALID")
            levels(b["bids"], "bids")
            levels(b["asks"], "asks")
            if b["bids"][0][0] >= b["asks"][0][0]:
                raise ValueError("DEX_STRESS_BOOK_CROSSED")
        # Conflicting simultaneous observations must not be resolved by insertion order.
        for rows, key in (
            (
                self.quotes,
                lambda x: (
                    x["observed_at"],
                    x["quote"]["chain_id"],
                    x["quote"]["sell_token"],
                    x["quote"]["buy_token"],
                    x["quote"]["sell_amount_raw"],
                    x["quote"]["buy_amount_raw"],
                ),
            ),
            (self.books, lambda x: (x["received_at"], x["venue"], x["symbol"])),
        ):
            seen = {}
            for r in rows:
                k = key(r)
                if k in seen and seen[k] != r:
                    raise ValueError("DEX_STRESS_TAPE_CONFLICT")
                seen[k] = r

    def book(self, p, at, age):
        for b in reversed(self.books[: bisect.bisect_right(self.book_times, at)]):
            if (b["venue"], b["symbol"]) != (p["cex_venue"], p["cex_symbol"]):
                continue
            if (
                b["contract_size"] != p["contract_size"]
                or not 0 <= at - b["book_ts"] <= age
                or not 0 <= at - b["received_at"] <= age
            ):
                return None
            return b

    def exit(self, p, at):
        for row in reversed(self.quotes[: bisect.bisect_right(self.quote_times, at)]):
            q = row["quote"]
            pair = (
                (p["asset"], p["stable"]) if p["forward"] else (p["stable"], p["asset"])
            )
            if (
                q["chain_id"] != p["chain_id"]
                or (q["sell_token"], q["buy_token"]) != pair
            ):
                continue
            raw = q["sell_amount_raw"] if p["forward"] else q["buy_amount_raw"]
            if str(raw) != p["asset_amount_raw"] or q["quote_mode"] != (
                "exact_in" if p["forward"] else "exact_out"
            ):
                continue
            if not 0 <= at - q["ts"] <= 15 or at - row["observed_at"] > 15:
                return None
            return row


def simulate(p, tape, scenario):
    p = dict(p)
    for key in (
        "opened_at",
        "closed_at",
        "base_qty",
        "contract_size",
        "cex_contracts",
        "entry_cash",
        "entry_gas",
        "fee_rate",
        "safety",
    ):
        if not math.isfinite(float(p[key])):
            raise ValueError("DEX_STRESS_POSITION_INVALID")
    if (
        p["base_qty"] <= 0
        or p["contract_size"] <= 0
        or p["closed_at"] < p["opened_at"]
        or not 0 <= p["fee_rate"] <= 0.1
        or min(p["entry_gas"], p["safety"]) < 0
        or p["cex_side"] != ("sell" if p["forward"] else "buy")
        or not math.isclose(
            p["base_qty"], p["cex_contracts"] * p["contract_size"], rel_tol=1e-12
        )
    ):
        raise ValueError("DEX_STRESS_POSITION_SCOPE_INVALID")
    q = p["entry_dex"]
    if not q["ts"] <= q["received_at"] <= p["opened_at"]:
        raise ValueError("DEX_STRESS_ENTRY_LOOKAHEAD")
    rows, used = [], []
    native = Decimal(0)
    inventory = 0
    cash = fees = gas = 0.0
    unknown = False
    liquidity = {}

    def result(status, reason):
        flat = native == 0 and inventory == 0 and not unknown
        return dict(
            status=status,
            reason=reason,
            net=cash - fees - gas - p["safety"] if flat else None,
            cex_contracts_remaining=float(native),
            asset_delta_raw=None if unknown else str(inventory),
            unknown_wallet=unknown,
            cash=cash,
            fees=fees,
            gas=gas,
            rows=rows,
            used=used,
            scenario=asdict(scenario),
            mode="DEX_QUOTE_BOUND_EXECUTION_MODEL_NO_FUNDING",
            live_allowed=False,
            ledger_allowed=False,
        )

    arrival = p["opened_at"] + scenario.dex_confirmation
    if scenario.entry_outcome == "unknown":
        unknown = True
        return result("UNRESOLVED", "ENTRY_RECEIPT_UNKNOWN")
    gas += p["entry_gas"]
    if scenario.entry_outcome == "revert":
        return result("ABORTED_MODEL", "ENTRY_REVERT_ASSUMPTION")
    if arrival - q["ts"] > 15:
        unknown = True
        return result("UNRESOLVED", "ENTRY_QUOTE_EXPIRED_EXECUTION_UNKNOWN")
    inventory = integer(p["asset_amount_raw"], "DEX_STRESS_ASSET") * (
        1 if p["forward"] else -1
    )
    cash += -p["entry_cash"] if p["forward"] else p["entry_cash"]
    rows.append(
        dict(
            stage="dex_entry",
            at=arrival,
            asset_delta_raw=str(inventory),
            cash=cash,
            gas=p["entry_gas"],
            assumption="ATOMIC_QUOTE_BOUND_FILL",
        )
    )
    used.append(dict(kind="entry_quote", quote=q))

    def future(side, contracts, at, stage):
        nonlocal cash, fees, native
        b = tape.book(p, at, scenario.max_age)
        row = dict(stage=stage, at=at, requested=contracts, filled=0.0, side=side)
        rows.append(row)
        if b is None:
            row["reason"] = "BOOK_UNAVAILABLE_STALE_OR_CHANGED"
            return 0
        snapshot_key = (b["venue"], b["symbol"], b["book_ts"], b["received_at"], side)
        depth = liquidity.setdefault(
            snapshot_key, [list(r) for r in b["asks" if side == "buy" else "bids"]]
        )
        fill = walk(depth, contracts)
        remaining = fill.filled
        for level in depth:
            taken = min(remaining, level[1])
            level[1] -= taken
            remaining -= taken
        row.update(filled=fill.filled, price=fill.price)
        if fill.filled:
            sign = 1 if side == "buy" else -1
            native += Decimal(str(sign)) * Decimal(str(fill.filled))
            value = fill.filled * p["contract_size"] * fill.price
            cash -= sign * value
            fees += value * p["fee_rate"]
        used.append(dict(kind="book", payload=b))
        return fill.filled

    filled = future(
        p["cex_side"], p["cex_contracts"], arrival + scenario.cex_latency, "cex_entry"
    )
    partial = filled < p["cex_contracts"] * (1 - 1e-12)
    exit_at = (
        arrival + scenario.cex_latency + scenario.recovery_latency
        if partial
        else max(p["closed_at"], arrival + scenario.cex_latency)
    )
    # Close confirmed CEX quantity before restoring the original DEX inventory.
    if native:
        future(
            "sell" if native > 0 else "buy",
            float(abs(native)),
            exit_at + scenario.cex_latency,
            "cex_unwind" if partial else "cex_exit",
        )
        if native:
            future(
                "sell" if native > 0 else "buy",
                float(abs(native)),
                exit_at + scenario.cex_latency + scenario.recovery_latency,
                "cex_recovery",
            )
    dex_at = exit_at + scenario.cex_latency + scenario.dex_confirmation
    if scenario.exit_outcome == "unknown":
        unknown = True
        return result("UNRESOLVED", "EXIT_RECEIPT_UNKNOWN")
    quote = tape.exit(p, exit_at + scenario.cex_latency)
    if quote is None:
        return result("RESIDUAL_MODEL", "EXACT_REVERSE_QUOTE_UNAVAILABLE")
    used.append(dict(kind="exit_quote", payload=quote))
    gas += quote["gas"]
    if scenario.exit_outcome == "revert":
        return result("RESIDUAL_MODEL", "EXIT_REVERT_ASSUMPTION")
    if dex_at - quote["quote"]["ts"] > 15:
        unknown = True
        return result("UNRESOLVED", "EXIT_QUOTE_EXPIRED_EXECUTION_UNKNOWN")
    q = quote["quote"]
    raw = q["min_buy_amount_raw"] if p["forward"] else q["max_sell_amount_raw"]
    value = float(
        Decimal(integer(raw, "DEX_STRESS_CASH")) / Decimal(10 ** p["stable_decimals"])
    )
    cash += value if p["forward"] else -value
    inventory = 0
    rows.append(
        dict(
            stage="dex_exit",
            at=dex_at,
            cash=value if p["forward"] else -value,
            gas=quote["gas"],
            assumption="ATOMIC_QUOTE_BOUND_FILL",
        )
    )
    return result(
        "CLOSED_MODEL" if native == 0 else "RESIDUAL_MODEL",
        "PARTIAL_ENTRY_UNWIND" if partial else "NORMAL_MODEL_EXIT",
    )


async def build(path, limit=50, clock=time.time):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("DEX_STRESS_LIMIT_INVALID")
    await History(path, clock).init()
    async with aiosqlite.connect(path) as d:
        async with d.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='cex_dex_paper'"
        ) as c:
            if not await c.fetchone():
                return dict(positions=0, results=[], reason="DEX_PAPER_HISTORY_MISSING")
        await d.execute("BEGIN")
        async with d.execute(
            "SELECT payload FROM cex_dex_paper WHERE status='CLOSED' ORDER BY id DESC LIMIT ?",
            (limit,),
        ) as c:
            positions = [json.loads(r[0]) for r in await c.fetchall()]
        outputs = []
        for p in positions:
            from .cex_dex_replay_lineage import validate as validate_lineage

            try:
                validate_lineage(p, p["last_mark"], p["closed_at"])
            except (ValueError, KeyError, TypeError) as error:
                outputs.append(
                    dict(
                        position_id=p["id"],
                        result=dict(
                            status="EXCLUDED",
                            net=None,
                            scenario=dict(name="VALIDATION"),
                            reason=(
                                str(error)
                                if isinstance(error, ValueError)
                                else "PAYLOAD_INVALID"
                            ),
                        ),
                    )
                )
                continue
            async with d.execute(
                "SELECT payload FROM dex_quote_history WHERE chain_id=? AND observed_at>=? AND observed_at<=? ORDER BY observed_at,id",
                (p["chain_id"], p["opened_at"] - 15, p["closed_at"] + 60),
            ) as c:
                quotes = [json.loads(r[0]) for r in await c.fetchall()]
            async with d.execute(
                "SELECT payload FROM cex_dex_paper_marks WHERE position_id=? ORDER BY ts,id",
                (p["id"],),
            ) as c:
                marks = [json.loads(r[0]) for r in await c.fetchall()]
            books = [p["entry_cex"]] + [m["exit_cex"] for m in marks if "exit_cex" in m]
            # Dense scanner books, when available, are converted back to native units.
            async with d.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='market_books'"
            ) as c:
                has_books = await c.fetchone()
            if has_books:
                async with d.execute(
                    "SELECT payload FROM market_books WHERE venue=? AND symbol=? AND received_at>=? AND received_at<=? ORDER BY received_at,id",
                    (
                        p["cex_venue"],
                        p["cex_symbol"],
                        p["opened_at"] - 2,
                        p["closed_at"] + 60,
                    ),
                ) as c:
                    for r in await c.fetchall():
                        b = json.loads(r[0])
                        size = b["instrument"]["contract_size"]
                        spec = b["instrument"]
                        if (
                            not spec["contract"]
                            or not spec["linear"]
                            or spec["quote"] != "USDT"
                            or spec["settle"] != "USDT"
                            or spec["base"] != p["cex_symbol"].split("/")[0]
                        ):
                            continue
                        books.append(
                            dict(
                                venue=b["venue"],
                                symbol=b["symbol"],
                                book_ts=b["book_ts"],
                                received_at=b["received_at"],
                                contract_size=size,
                                bids=[[px, qty / size] for px, qty in b["bids"]],
                                asks=[[px, qty / size] for px, qty in b["asks"]],
                            )
                        )
            # Duplicate identical quote/book proofs are observational duplicates.
            books = list({json.dumps(b, sort_keys=True): b for b in books}.values())
            try:
                tape = Tape(quotes, books)
                for s in SCENARIOS:
                    outputs.append(
                        dict(position_id=p["id"], result=simulate(p, tape, s))
                    )
            except (ValueError, KeyError, TypeError) as error:
                outputs.append(
                    dict(
                        position_id=p["id"],
                        result=dict(
                            status="EXCLUDED",
                            net=None,
                            scenario=dict(name="VALIDATION"),
                            reason=(
                                str(error)
                                if isinstance(error, ValueError)
                                else "PAYLOAD_INVALID"
                            ),
                        ),
                    )
                )
        await d.commit()
        report = dict(
            positions=len(positions),
            results=outputs,
            reason="MODEL_ONLY_NO_FUNDING_NO_LEDGER",
        )
        cur = await d.execute(
            "INSERT INTO dex_stress_runs(created_at,payload) VALUES(?,?)",
            (clock(), json.dumps(report)),
        )
        for row in outputs:
            r = row["result"]
            await d.execute(
                "INSERT INTO dex_stress_results(run_id,position_id,scenario,status,net,payload) VALUES(?,?,?,?,?,?)",
                (
                    cur.lastrowid,
                    row["position_id"],
                    r["scenario"]["name"],
                    r["status"],
                    r["net"],
                    json.dumps(r),
                ),
            )
        await d.commit()
    return report


def render(report):
    out = [
        "⏱ <b>CEX/DEX · stress исполнения</b>",
        "<i>DEX: атомарный fill по границе котировки / revert / UNKNOWN. CEX: последовательный IOC с частичным fill и bounded recovery. Это сценарная модель, без funding и зачисления в Ledger.</i>",
        f"Позиций: {report['positions']}",
    ]
    groups = {}
    for row in report["results"]:
        r = row["result"]
        groups.setdefault(r["scenario"]["name"], []).append(r)
    for name, rows in groups.items():
        known = [r["net"] for r in rows if r["net"] is not None]
        out.append(
            f"\n<b>{escape(name)}</b> · NET модели {sum(known):+.4f} USD\nПолных результатов: {len(known)}/{len(rows)}; остальные — остаток или неполные данные."
        )
    out.append(
        "\nКотировка не доказывает реальный swap. При редкой записи задержанные сценарии останутся неполными."
    )
    return "\n".join(out)
