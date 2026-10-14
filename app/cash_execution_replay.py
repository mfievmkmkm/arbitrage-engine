"""Sequential offline cash-leg IOC stress. Never submits orders or credits capital."""

import json
import math
import time
from collections import Counter
from dataclasses import asdict
from html import escape

import aiosqlite

from .execution_book_replay import SCENARIOS, Tape, STATUS_LABELS, SCENARIO_LABELS
from .execution_sim import walk

SCHEMA = """
CREATE TABLE IF NOT EXISTS cash_execution_runs(id INTEGER PRIMARY KEY,created_at REAL,strategy TEXT,payload TEXT);
CREATE TABLE IF NOT EXISTS cash_execution_results(id INTEGER PRIMARY KEY,run_id INTEGER,position_id INTEGER,scenario TEXT,status TEXT,net REAL,payload TEXT);
"""
STRATEGIES = {"spot_futures": "Спот ↔ Фьючерсы", "spot_spot": "Спот ↔ Спот"}


def number(value, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("POSITION_INVALID")
    value = float(value)
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError("POSITION_INVALID")
    return value


def normalize(strategy, p):
    if strategy not in STRATEGIES:
        raise ValueError("STRATEGY_INVALID")
    qty = number(p["base_qty"], True)
    opened, closed = number(p["opened_at"]), number(p["closed_at"])
    rate = number(p["entry_fee_pct"]) / 400
    if closed < opened or rate >= 1:
        raise ValueError("POSITION_INVALID")
    if strategy == "spot_futures":
        if p["direction"] != "LONG_SPOT_SHORT_FUTURE":
            raise ValueError("BORROWING_NOT_MODELED")
        base, venue = p["base"], p["exchange"]
        if not isinstance(base, str) or not base or "/" in base or ":" in base:
            raise ValueError("POSITION_INVALID")
        legs = [(venue, base + "/USDT"), (venue, base + "/USDT:USDT")]
        safety = number(number(p["notional"], True) * number(p["safety_pct"]) / 100)
    else:
        symbol = p["symbol"]
        if (
            not isinstance(symbol, str)
            or not symbol.endswith("/USDT")
            or symbol.count("/") != 1
            or ":" in symbol
        ):
            raise ValueError("POSITION_INVALID")
        base = symbol.split("/")[0]
        legs = [(p["buy"], symbol), (p["sell"], symbol)]
        if p["buy"] == p["sell"] or not base:
            raise ValueError("POSITION_INVALID")
        evidence = p.get("inventory_evidence", {})
        if (
            not isinstance(evidence, dict)
            or not {"sell_quote", "buy_quote", "sell_base"} <= evidence.keys()
        ):
            raise ValueError("INVENTORY_UNVERIFIED")
        number(evidence["sell_quote"])
        required = qty * number(p["entry_buy"], True) * (1 + rate)
        if (
            evidence.get("mode") != "PREFUNDED_PAPER_INVENTORY"
            or evidence.get("buy") != p["buy"]
            or evidence.get("sell") != p["sell"]
            or evidence.get("asset") != base
            or number(evidence["buy_quote"]) < required
            or number(evidence["sell_base"]) < qty
        ):
            raise ValueError("INVENTORY_UNVERIFIED")
        safety = number(p["safety"])
    if any(not isinstance(v, str) or not v for v, _ in legs):
        raise ValueError("POSITION_INVALID")
    return qty, opened, closed, rate, safety, base, legs


def simulate(strategy, position, tape, scenario):
    qty, opened, closed, rate, safety, base, legs = normalize(strategy, position)
    exposure = [0.0, 0.0]
    quote_balances = (
        [
            position["inventory_evidence"]["buy_quote"],
            position["inventory_evidence"]["sell_quote"],
        ]
        if strategy == "spot_spot"
        else None
    )
    orders, identities, consumed = [], {}, {}
    cash = fees = 0.0
    tolerance = min(qty * 1e-8, 1e-10)

    def order(leg, side, requested, arrival, phase):
        nonlocal cash, fees
        number(arrival)
        venue, symbol = legs[leg]
        row = dict(
            leg=leg,
            venue=venue,
            symbol=symbol,
            side=side,
            requested=requested,
            arrival=arrival,
            phase=phase,
            filled=0.0,
            price=None,
            fee=0.0,
            reason=None,
        )
        orders.append(row)
        book = tape.at(venue, symbol, arrival, scenario.max_age)
        if book is None:
            row["reason"] = "BOOK_UNAVAILABLE"
            return 0.0
        row["book_evidence"] = book
        instrument = book["instrument"]
        derivative = strategy == "spot_futures" and leg == 1
        identity = dict(instrument)
        if (
            instrument.get("exchange") != venue
            or instrument.get("symbol") != symbol
            or instrument["base"] != base
            or instrument["quote"] != "USDT"
            or (
                derivative
                and not (
                    instrument.get("contract") is True
                    and instrument.get("linear") is True
                    and instrument.get("settle") == "USDT"
                )
            )
            or (not derivative and instrument.get("spot") is not True)
        ):
            row["reason"] = "INSTRUMENT_MISMATCH"
            return 0.0
        if leg in identities and identities[leg] != identity:
            row["reason"] = "INSTRUMENT_CHANGED"
            return 0.0
        identities[leg] = identity
        # Reusing a snapshot cannot replenish its displayed IOC liquidity.
        side_key = "asks" if side == "BUY" else "bids"
        key = (venue, symbol, book["book_ts"], side_key)
        levels = consumed.setdefault(key, [list(x) for x in book[side_key]])
        affordable = requested
        if quote_balances is not None and side == "BUY":
            balance, affordable, remaining_qty = (
                max(0.0, quote_balances[leg]),
                0.0,
                requested,
            )
            for price, amount in levels:
                units = min(amount, remaining_qty, balance / (price * (1 + rate)))
                affordable += units
                remaining_qty -= units
                balance = max(0.0, balance - units * price * (1 + rate))
        if affordable <= 0:
            row["reason"] = "QUOTE_FUNDS_UNAVAILABLE"
            return 0.0
        fill = walk(levels, affordable)
        remaining = fill.filled
        for level in levels:
            used = min(level[1], remaining)
            level[1] -= used
            remaining -= used
        row.update(
            filled=fill.filled,
            price=fill.price,
            reason="FILLED" if requested - fill.filled <= tolerance else "PARTIAL",
        )
        if requested - affordable > tolerance:
            row["reason"] = "QUOTE_FUNDS_LIMIT"
        if fill.filled:
            notional = fill.filled * fill.price
            fee = notional * rate
            sign = 1 if side == "BUY" else -1
            exposure[leg] += sign * fill.filled
            cash -= sign * notional
            fees += fee
            if quote_balances is not None:
                quote_balances[leg] -= sign * notional + fee
            row["fee"] = fee
            if not all(math.isfinite(x) for x in (cash, fees, *exposure)):
                raise ValueError("MODEL_OVERFLOW")
        return fill.filled

    # Buy cash first; the follow-up can hedge only its known actual fill.
    first_at = opened + scenario.long_latency
    first = order(0, "BUY", qty, first_at, "ENTRY")
    second_at = first_at + scenario.short_latency
    second = (
        order(1, "SELL", first, second_at, "ENTRY")
        if first > tolerance and second_at <= closed
        else 0.0
    )
    complete = (
        qty - first <= tolerance and qty - second <= tolerance and second_at <= closed
    )
    recovery = False
    arrival = max(first_at, second_at if second else first_at)
    if complete:
        # Restore the short/source inventory before selling the acquired cash.
        arrival = closed + scenario.short_latency
        restored = order(1, "BUY", -exposure[1], arrival, "EXIT")
        arrival += scenario.long_latency
        if restored > tolerance:
            order(0, "SELL", min(exposure[0], restored), arrival, "EXIT")
    # Abort incomplete entries, and recover exit leftovers, in bounded rounds.
    # Never sell cash while a larger derivative/source short remains outstanding.
    for attempt in range(3):
        if all(abs(x) <= tolerance for x in exposure):
            break
        recovery = True
        arrival += scenario.recovery_latency
        if exposure[1] < -tolerance:
            order(1, "BUY", -exposure[1], arrival, f"RECOVERY_{attempt + 1}")
        arrival += scenario.long_latency
        available = max(0.0, exposure[0] + exposure[1])
        if available > tolerance:
            order(0, "SELL", available, arrival, f"RECOVERY_{attempt + 1}")
    flat = all(abs(x) <= tolerance for x in exposure)
    any_fill = any(x["filled"] > 0 for x in orders)
    if not flat:
        status = "RESIDUAL_EXPOSURE"
    elif not any_fill:
        status = "NO_ENTRY"
    elif not complete:
        status = "LATE_ENTRY_ABORT_FLAT" if second_at > closed else "ENTRY_ABORT_FLAT"
    else:
        status = "CLOSED_WITH_RECOVERY" if recovery else "CLOSED"
    net = cash - fees - safety if flat and any_fill else None
    if net is not None and not math.isfinite(net):
        raise ValueError("MODEL_OVERFLOW")
    return dict(
        strategy=strategy,
        status=status,
        net=net,
        cashflow=cash,
        fees=fees,
        safety=safety,
        residual=exposure,
        quote_balances=quote_balances,
        orders=orders,
        funding=0,
        funding_mode="EXCLUDED_FROM_STRESS_MODEL",
        mode="SEQUENTIAL_CASH_RECORDED_IOC_MODEL",
        release_authorized=False,
    )


async def build(
    path, strategy, scenarios=SCENARIOS, limit=100, persist=True, clock=time.time
):
    scenarios = tuple(scenarios)
    if (
        strategy not in STRATEGIES
        or not scenarios
        or len({s.name for s in scenarios}) != len(scenarios)
        or isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 0 < limit <= 1000
    ):
        raise ValueError("REPLAY_PARAMETERS_INVALID")
    created = number(clock())
    report = dict(
        strategy=strategy,
        created_at=created,
        sample_size=0,
        excluded={},
        scenarios=[],
        status="HISTORY_NOT_COLLECTED",
        mode="SEQUENTIAL_CASH_RECORDED_IOC_MODEL",
        release_authorized=False,
        assumptions=[
            "IOC/taker public depth; no queue or private fill proof",
            "Paper entry_fee_pct/400 per fill, charged in modeled quote units",
            "Funding excluded; original Paper times; no capital credit or LIVE acceptance",
            "Native precision, account fees, minimum orders and inventory are not venue-certified",
            "Spot/Futures collateral assumed by Paper model; Spot/Spot quote balances enforced per venue",
            "Spot/Spot requires saved prefunded Paper inventory; reverse Spot/Futures borrowing excluded",
            "Sequential fills; at most three recovery rounds; snapshot-side depth consumed once per position",
        ],
    )
    results, excluded, positions = [], Counter(), []
    table = "spot_future_paper" if strategy == "spot_futures" else "spot_spot_paper"
    async with aiosqlite.connect(f"file:{path}?mode=ro", uri=True) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN")
        c = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0] for r in await c.fetchall()}
        if {table, "market_books"} <= tables:
            c = await db.execute(f"PRAGMA table_info({table})")
            columns = {r["name"] for r in await c.fetchall()}
            if {"payload", "closed_at"} <= columns:
                c = await db.execute(
                    f"SELECT * FROM {table} WHERE status!='OPEN' AND closed_at IS NOT NULL ORDER BY opened_at DESC,id DESC LIMIT ?",
                    (limit,),
                )
                for row in await c.fetchall():
                    try:
                        p = json.loads(row["payload"])
                        # SQL identity/times and durable payload must agree.
                        keys = (
                            (
                                "id",
                                "opened_at",
                                "status",
                                "base",
                                "exchange",
                                "direction",
                            )
                            if strategy == "spot_futures"
                            else (
                                "id",
                                "opened_at",
                                "closed_at",
                                "status",
                                "symbol",
                                "buy",
                                "sell",
                            )
                        )
                        if any(p[k] != row[k] for k in keys):
                            raise ValueError("POSITION_IDENTITY_CONFLICT")
                        if strategy == "spot_futures" and any(
                            p[k] != row[k]
                            for k in (
                                "base_qty",
                                "notional",
                                "spot_entry",
                                "future_entry",
                            )
                            if k in columns
                        ):
                            raise ValueError("POSITION_IDENTITY_CONFLICT")
                        p["closed_at"] = row["closed_at"]
                        normalize(strategy, p)
                        positions.append(p)
                    except (ValueError, KeyError, TypeError, OverflowError):
                        excluded["POSITION_OR_INVENTORY_UNVERIFIED"] += 1
                if positions:
                    max_age = max(s.max_age for s in scenarios)
                    horizon = max(
                        4 * s.long_latency + s.short_latency + 3 * s.recovery_latency
                        for s in scenarios
                    )
                    start = min(p["opened_at"] for p in positions) - max_age
                    end = (
                        max(max(p["opened_at"], p["closed_at"]) for p in positions)
                        + horizon
                    )
                    c = await db.execute(
                        "SELECT payload FROM market_books WHERE received_at BETWEEN ? AND ? ORDER BY received_at,id LIMIT 200001",
                        (start, end),
                    )
                    raw = await c.fetchall()
                    if len(raw) > 200000:
                        report["status"] = "BOOK_HISTORY_TOO_LARGE"
                    else:
                        try:
                            tape = Tape([json.loads(r[0]) for r in raw])
                        except (ValueError, TypeError, KeyError):
                            report["status"] = "BOOK_HISTORY_INVALID"
                        else:
                            for p in positions:
                                try:
                                    batch = [
                                        dict(
                                            simulate(strategy, p, tape, s),
                                            position=p,
                                            position_id=p["id"],
                                            scenario=s.name,
                                        )
                                        for s in scenarios
                                    ]
                                except (ValueError, TypeError, KeyError, OverflowError):
                                    excluded["POSITION_OR_MODEL_INVALID"] += 1
                                else:
                                    results.extend(batch)
                            report["status"] = (
                                "MODELED" if results else "INSUFFICIENT_DATA"
                            )
                else:
                    report["status"] = "INSUFFICIENT_DATA"
    report["excluded"] = dict(excluded)
    report["sample_size"] = len({r["position_id"] for r in results})
    for s in scenarios:
        rows = [r for r in results if r["scenario"] == s.name]
        nets = [r["net"] for r in rows if r["net"] is not None]
        net = math.fsum(nets) if nets else None
        report["scenarios"].append(
            dict(
                parameters=asdict(s),
                statuses=dict(Counter(r["status"] for r in rows)),
                completed=len(nets),
                net=net,
                fees=math.fsum(r["fees"] for r in rows),
                residual_positions=sum(
                    r["status"] == "RESIDUAL_EXPOSURE" for r in rows
                ),
                unavailable_orders=sum(
                    o["reason"] == "BOOK_UNAVAILABLE" for r in rows for o in r["orders"]
                ),
            )
        )
    if persist:
        async with aiosqlite.connect(path) as db:
            await db.executescript(SCHEMA)
            await db.execute("BEGIN IMMEDIATE")
            c = await db.execute(
                "INSERT INTO cash_execution_runs(created_at,strategy,payload) VALUES(?,?,?)",
                (created, strategy, json.dumps(report, allow_nan=False)),
            )
            run_id = c.lastrowid
            for r in results:
                await db.execute(
                    "INSERT INTO cash_execution_results(run_id,position_id,scenario,status,net,payload) VALUES(?,?,?,?,?,?)",
                    (
                        run_id,
                        r["position_id"],
                        r["scenario"],
                        r["status"],
                        r["net"],
                        json.dumps(r, allow_nan=False),
                    ),
                )
            await db.commit()
        report["run_id"] = run_id
    return report


def render(report):
    out = [
        f"🧪 <b>{STRATEGIES[report['strategy']]} · исполнение</b>",
        "<i>Последовательная модель IOC по записанным стаканам.</i>",
        f"Сделок: {report['sample_size']} · исключено: {sum(report['excluded'].values())}",
    ]
    if report["status"] != "MODELED":
        out.append(escape(STATUS_LABELS.get(report["status"], report["status"])))
    for s in report["scenarios"]:
        name = s["parameters"]["name"]
        net = "не определён" if s["net"] is None else f"{s['net']:+.4f} USD"
        out.append(
            f"\n<b>{escape(SCENARIO_LABELS.get(name, name))}</b> · завершено: {s['completed']} · NET: {net}\nНезакрытых остатков: {s['residual_positions']} · нет свежего стакана: {s['unavailable_orders']}"
        )
        out.append(
            "Статусы: "
            + escape(
                ", ".join(
                    f"{STATUS_LABELS.get(k, k)}: {v}" for k, v in s["statuses"].items()
                )
                or "нет"
            )
        )
    out.append(
        "\n<i>Комиссии модельные, funding исключён. Не подтверждает реальные fills, доступный капитал или допуск к LIVE. Обратный Spot/Futures без подтверждённого займа исключён.</i>"
    )
    return "\n".join(out)
