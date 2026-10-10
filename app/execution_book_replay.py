"""Deterministic IOC/taker stress model. No network, keys, orders or Ledger writes."""

import bisect
import json
import math
import time
from collections import Counter
from dataclasses import dataclass, asdict
from html import escape
import aiosqlite
from .book_history import valid_book, Store
from .execution_sim import walk
from .engine import FEE_BPS


@dataclass(frozen=True)
class Scenario:
    name: str
    long_latency: float
    short_latency: float
    recovery_latency: float = 0.5
    max_age: float = 1.5

    def __post_init__(self):
        if (
            not all(
                math.isfinite(x) and x >= 0
                for x in (self.long_latency, self.short_latency, self.recovery_latency)
            )
            or not math.isfinite(self.max_age)
            or self.max_age <= 0
        ):
            raise ValueError("EXECUTION_SCENARIO_INVALID")


SCENARIOS = (
    Scenario("BASE", 0, 0),
    Scenario("ASYMMETRIC", 0.15, 0.5),
    Scenario("SLOW", 1, 1),
)


class Tape:
    def __init__(self, books):
        self.routes = {}
        for b in books:
            if not valid_book(b):
                raise ValueError("BOOK_HISTORY_INVALID")
            self.routes.setdefault((b["venue"], b["symbol"]), []).append(b)
        for values in self.routes.values():
            values.sort(key=lambda b: (b["received_at"], b["book_ts"]))
            seen = {}
            for b in values:
                key = (b["received_at"], b["book_ts"])
                if key in seen and seen[key] != b:
                    raise ValueError("BOOK_HISTORY_CONFLICT")
                seen[key] = b
        self.times = {
            key: [b["received_at"] for b in values]
            for key, values in self.routes.items()
        }

    def at(self, venue, symbol, arrival, max_age):
        key = (venue, symbol)
        values = self.routes.get(key, [])
        idx = bisect.bisect_right(self.times.get(key, []), arrival) - 1
        if idx < 0:
            return None
        b = values[idx]
        return (
            b
            if 0 <= arrival - b["book_ts"] <= max_age
            and 0 <= arrival - b["received_at"] <= max_age
            else None
        )


def simulate(position, tape, scenario, fee_rates):
    qty = float(position["base_qty"])
    opened = float(position["opened_at"])
    closed = float(position["closed_at"])
    long, short = position["buy"], position["sell"]
    symbol = position["symbol"]
    safety = float(position["safety_usd"])
    if (
        not all(math.isfinite(x) for x in (qty, opened, closed, safety))
        or qty <= 0
        or closed < opened
        or safety < 0
        or long == short
    ):
        raise ValueError("EXECUTION_POSITION_INVALID")
    if any(
        v not in fee_rates or not math.isfinite(fee_rates[v]) or fee_rates[v] < 0
        for v in (long, short)
    ):
        raise ValueError("EXECUTION_FEE_UNKNOWN")
    tolerance = min(qty * 1e-8, 1e-10)
    rows = []
    exposure = {long: 0.0, short: 0.0}
    cash = fees = 0.0
    identity = {}

    def order(venue, side, amount, arrival, phase):
        nonlocal cash, fees
        if amount <= tolerance:
            return
        b = tape.at(venue, symbol, arrival, scenario.max_age)
        row = dict(
            venue=venue,
            side=side,
            requested=amount,
            arrival=arrival,
            phase=phase,
            filled=0.0,
            price=None,
            fee=0.0,
        )
        if b is None:
            row["reason"] = "BOOK_UNAVAILABLE_OR_STALE"
            rows.append(row)
            return
        instrument = {
            k: b["instrument"][k]
            for k in ("base", "quote", "settle", "contract_size", "contract", "linear")
        }
        if (
            instrument["contract"] is not True
            or instrument["linear"] is not True
            or instrument["settle"] != "USDT"
        ):
            row["reason"] = "FUTURES_REPLAY_INSTRUMENT_REQUIRED"
            rows.append(row)
            return
        if venue in identity and identity[venue] != instrument:
            row["reason"] = "INSTRUMENT_CHANGED"
            rows.append(row)
            return
        if identity and any(
            x["base"] != instrument["base"]
            or x["quote"] != instrument["quote"]
            or x["settle"] != instrument["settle"]
            for x in identity.values()
        ):
            row["reason"] = "INSTRUMENT_MISMATCH"
            rows.append(row)
            return
        identity[venue] = instrument
        f = walk(b["asks" if side == "BUY" else "bids"], amount)
        fee = f.filled * (f.price or 0) * fee_rates[venue]
        cash += (1 if side == "SELL" else -1) * f.filled * (f.price or 0)
        fees += fee
        exposure[venue] += f.filled * (1 if side == "BUY" else -1)
        row.update(
            filled=f.filled,
            price=f.price,
            fee=fee,
            book_ts=b["book_ts"],
            received_at=b["received_at"],
            book_evidence=b,
            reason=(
                "FILLED"
                if math.isclose(f.filled, amount, rel_tol=0, abs_tol=tolerance)
                else "IOC_PARTIAL"
            ),
        )
        rows.append(row)

    for venue, side, delay in sorted(
        ((long, "BUY", scenario.long_latency), (short, "SELL", scenario.short_latency)),
        key=lambda x: x[2],
    ):
        order(venue, side, qty, opened + delay, "ENTRY")
    complete = math.isclose(
        exposure[long], qty, rel_tol=0, abs_tol=tolerance
    ) and math.isclose(exposure[short], -qty, rel_tol=0, abs_tol=tolerance)
    latest = max(opened + scenario.long_latency, opened + scenario.short_latency)
    deadline_missed = complete and closed < latest
    if deadline_missed:
        complete = False
    if complete:
        for venue, side, delay in sorted(
            (
                (long, "SELL", scenario.long_latency),
                (short, "BUY", scenario.short_latency),
            ),
            key=lambda x: x[2],
        ):
            order(venue, side, abs(exposure[venue]), closed + delay, "EXIT")
        latest = max(closed + scenario.long_latency, closed + scenario.short_latency)
    entry_failed = not complete
    # IOC implies no working remainder; all surviving modeled exposure is flattened.
    if any(abs(x) > tolerance for x in exposure.values()):
        for venue in (long, short):
            order(
                venue,
                "SELL" if exposure[venue] > 0 else "BUY",
                abs(exposure[venue]),
                latest + scenario.recovery_latency,
                "RECOVERY",
            )
    flat = all(abs(x) <= tolerance for x in exposure.values())
    filled = any(x["filled"] > 0 for x in rows)
    status = (
        "NO_ENTRY"
        if not filled
        else (
            "RESIDUAL_EXPOSURE"
            if not flat
            else (
                "ENTRY_ABORT_FLAT"
                if entry_failed
                else (
                    "CLOSED_WITH_RECOVERY"
                    if any(x["phase"] == "RECOVERY" for x in rows)
                    else "CLOSED"
                )
            )
        )
    )
    if deadline_missed and flat:
        status = "LATE_ENTRY_ABORT_FLAT"
    return dict(
        status=status,
        net=cash - fees - safety if flat and filled else None,
        cashflow=cash,
        fees=fees,
        safety=safety,
        funding=0,
        funding_mode="EXCLUDED_FROM_STRESS_MODEL",
        residual=exposure,
        orders=rows,
        mode="RECORDED_BOOK_IOC_MODEL",
        release_authorized=False,
    )


async def build(path, scenarios=SCENARIOS, limit=100, persist=True, clock=time.time):
    if (
        not scenarios
        or limit <= 0
        or len({s.name for s in scenarios}) != len(scenarios)
    ):
        raise ValueError("EXECUTION_REPLAY_CONFIG_INVALID")
    report = dict(
        mode="RECORDED_BOOK_IOC_MODEL",
        release_authorized=False,
        created_at=clock(),
        sample_size=0,
        excluded={},
        scenarios=[],
        fee_rates={v: rate / 10000 for v, rate in FEE_BPS.items()},
        assumptions=[
            "Public REST/WS snapshots; no intrabook queue or liquidity guarantee",
            "IOC/taker only; supplied/model fees, funding excluded",
            "Original Paper entry and exit times; no parameter selection or Ledger credit",
            "Quantity in base units; precision/limits/queue and competing liquidity are not exchange-certified",
        ],
    )
    async with aiosqlite.connect(path) as d:
        d.row_factory = aiosqlite.Row
        await d.execute("BEGIN")
        async with d.execute("SELECT name FROM sqlite_master WHERE type='table'") as c:
            tables = {r[0] for r in await c.fetchall()}
        if not {"paper_positions", "market_books"} <= tables:
            return dict(report, status="HISTORY_NOT_COLLECTED")
        async with d.execute(
            "SELECT * FROM paper_positions WHERE status='CLOSED' ORDER BY opened_at DESC,id DESC LIMIT ?",
            (limit,),
        ) as c:
            positions = [dict(r) for r in await c.fetchall()]
        if not positions:
            return dict(report, status="INSUFFICIENT_DATA")
        accepted = []
        for p in positions:
            try:
                if (
                    any(
                        p[k] is None or not math.isfinite(float(p[k]))
                        for k in (
                            "opened_at",
                            "closed_at",
                            "base_qty",
                            "safety_usd",
                            "entry_fees_usd",
                        )
                    )
                    or p["base_qty"] <= 0
                    or p["closed_at"] < p["opened_at"]
                ):
                    raise ValueError()
                accepted.append(p)
            except (ValueError, TypeError, KeyError):
                report["excluded"]["POSITION_OR_FEES_UNVERIFIED"] = (
                    report["excluded"].get("POSITION_OR_FEES_UNVERIFIED", 0) + 1
                )
        positions = accepted
        if not positions:
            return dict(report, status="INSUFFICIENT_DATA")
        min_ts = min(float(p["opened_at"]) for p in positions) - max(
            s.max_age for s in scenarios
        )
        max_ts = max(float(p["closed_at"]) for p in positions) + max(
            max(s.long_latency, s.short_latency) + s.recovery_latency for s in scenarios
        )
        async with d.execute(
            "SELECT payload FROM market_books WHERE received_at BETWEEN ? AND ? ORDER BY received_at,id LIMIT 200001",
            (min_ts, max_ts),
        ) as c:
            raw = await c.fetchall()
        if len(raw) > 200000:
            return dict(report, status="BOOK_HISTORY_TOO_LARGE")
        try:
            tape = Tape([json.loads(r[0]) for r in raw])
        except (ValueError, TypeError, KeyError):
            return dict(report, status="BOOK_HISTORY_INVALID")
    results = []
    eligible = set()
    excluded = Counter(report["excluded"])
    for p in sorted(positions, key=lambda x: (x["opened_at"], x["id"])):
        try:
            position_results = []
            for scenario in scenarios:
                result = simulate(
                    p, tape, scenario, {v: rate / 10000 for v, rate in FEE_BPS.items()}
                )
                position_results.append(
                    dict(
                        position_id=p["id"],
                        position={
                            k: p[k]
                            for k in (
                                "id",
                                "symbol",
                                "buy",
                                "sell",
                                "opened_at",
                                "closed_at",
                                "base_qty",
                                "safety_usd",
                            )
                        },
                        scenario=scenario.name,
                        **result,
                    )
                )
            results.extend(position_results)
            eligible.add(p["id"])
        except (ValueError, TypeError, KeyError):
            excluded["POSITION_OR_FEES_UNVERIFIED"] += 1
    for scenario in scenarios:
        xs = [x for x in results if x["scenario"] == scenario.name]
        nets = [x["net"] for x in xs if x["net"] is not None]
        report["scenarios"].append(
            dict(
                parameters=asdict(scenario),
                statuses=dict(Counter(x["status"] for x in xs)),
                completed=len(nets),
                net=sum(nets) if nets else None,
                fees=sum(x.get("fees", 0) for x in xs),
                residual_positions=sum(
                    x["status"] in ("RESIDUAL_EXPOSURE", "EXIT_BEFORE_ENTRY_COMPLETE")
                    for x in xs
                ),
                unavailable_orders=sum(
                    o["reason"] == "BOOK_UNAVAILABLE_OR_STALE"
                    for x in xs
                    for o in x["orders"]
                ),
            )
        )
    report.update(
        status="MODELED" if results else "INSUFFICIENT_DATA",
        sample_size=len(eligible),
        excluded=dict(excluded),
    )
    if persist:
        await Store(path).init()
        async with aiosqlite.connect(path) as d:
            cursor = await d.execute(
                "INSERT INTO execution_replay_runs(created_at,payload) VALUES(?,?)",
                (report["created_at"], json.dumps(report, allow_nan=False)),
            )
            for r in results:
                await d.execute(
                    "INSERT INTO execution_replay_results(run_id,position_id,scenario,status,net,payload) VALUES(?,?,?,?,?,?)",
                    (
                        cursor.lastrowid,
                        r["position_id"],
                        r["scenario"],
                        r["status"],
                        r["net"],
                        json.dumps(r, allow_nan=False),
                    ),
                )
            await d.commit()
        report["run_id"] = cursor.lastrowid
    return report


STATUS_LABELS = {
    "CLOSED": "закрыто",
    "CLOSED_WITH_RECOVERY": "закрыто после восстановления",
    "ENTRY_ABORT_FLAT": "вход отменён, остаток закрыт",
    "LATE_ENTRY_ABORT_FLAT": "запоздалый вход закрыт",
    "NO_ENTRY": "вход не состоялся",
    "RESIDUAL_EXPOSURE": "остаток не закрыт",
    "HISTORY_NOT_COLLECTED": "стаканы ещё не записаны",
    "INSUFFICIENT_DATA": "мало пригодных сделок",
    "BOOK_HISTORY_INVALID": "история стаканов повреждена",
    "BOOK_HISTORY_TOO_LARGE": "выборка стаканов превышает лимит",
}
SCENARIO_LABELS = {
    "BASE": "Без задержки",
    "ASYMMETRIC": "Разные задержки ног",
    "SLOW": "Задержка обеих ног",
}


def render(report):
    out = [
        "🧪 <b>Исполнение · проверка задержек</b>",
        "<i>Futures/Futures: виртуальные IOC по записанным публичным стаканам. Реальные заявки не отправляются.</i>",
        f"\nСделок в выборке: {report['sample_size']}",
    ]
    if report["status"] != "MODELED":
        out.append(
            "Недостаточно пригодной истории: "
            + escape(STATUS_LABELS.get(report["status"], report["status"]))
        )
    for s in report["scenarios"]:
        p = s["parameters"]
        net = "не определён" if s["net"] is None else f"{s['net']:+.4f} USD"
        out.append(
            f"\n<b>{escape(SCENARIO_LABELS.get(p['name'],p['name']))}</b> · задержка {p['long_latency']:.2f}/{p['short_latency']:.2f} сек\nЗавершённых моделей: {s['completed']} · NET: {net}\nНепогашенный остаток: {s['residual_positions']} · нет свежего стакана: {s['unavailable_orders']}"
        )
        out.append(
            "Статусы: "
            + escape(
                ", ".join(
                    f"{STATUS_LABELS.get(k,k)}: {v}" for k, v in s["statuses"].items()
                )
                or "нет"
            )
        )
    out.append(
        "\n<i>Funding исключён, комиссии модельные. REST/WS не доказывают fills и очередь. Результат не меняет капитал, настройки или допуск к LIVE.</i>"
    )
    return "\n".join(out)
