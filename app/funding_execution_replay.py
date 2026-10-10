"""Offline Funding IOC stress with per-leg public settlement accounting."""

import json
import math
import time
from collections import Counter
from dataclasses import asdict
from html import escape

import aiosqlite

from .cash_execution_replay import number
from .execution_book_replay import (
    SCENARIOS,
    Tape as BookTape,
    simulate as execute,
    STATUS_LABELS,
    SCENARIO_LABELS,
)
from .funding_rate_history import Tape as RateTape, finite

SCHEMA = """
CREATE TABLE IF NOT EXISTS funding_execution_runs(id INTEGER PRIMARY KEY,created_at REAL,payload TEXT);
CREATE TABLE IF NOT EXISTS funding_execution_results(id INTEGER PRIMARY KEY,run_id INTEGER,position_id INTEGER,scenario TEXT,status TEXT,net REAL,payload TEXT);
"""
MODE = "RECORDED_FUNDING_IOC_PUBLIC_RATE_MODEL"


def normalize(p):
    if isinstance(p["id"], bool) or not isinstance(p["id"], int) or p["id"] <= 0:
        raise ValueError("FUNDING_POSITION_INVALID")
    qty = number(p["base_qty"], True)
    opened, closed = number(p["opened_at"]), number(p["closed_at"])
    fee_rate = number(p["entry_fee_pct"]) / 400
    a, b = number(p["entry_buy"], True), number(p["entry_sell"], True)
    if (
        p["status"] != "CLOSED"
        or closed < opened
        or fee_rate >= 1
        or p["buy"] == p["sell"]
    ):
        raise ValueError("FUNDING_POSITION_INVALID")
    for key in ("buy", "sell", "symbol"):
        if not isinstance(p[key], str) or not p[key]:
            raise ValueError("FUNDING_POSITION_INVALID")
    if not math.isclose(
        number(p["entry_fees"]), qty * (a + b) * fee_rate, rel_tol=1e-8, abs_tol=1e-10
    ):
        raise ValueError("FUNDING_ENTRY_FEES_UNVERIFIED")
    mark = p["last_mark"]
    if not isinstance(mark, dict):
        raise ValueError("FUNDING_CLOSE_LINEAGE_UNVERIFIED")
    if (
        mark.get("mode") != "FUNDING_PUBLIC_HISTORY_MODEL"
        or mark.get("funding_known") is not True
        or number(mark["ts"]) != closed
        or number(mark["base_qty"], True) != qty
    ):
        raise ValueError("FUNDING_CLOSE_LINEAGE_UNVERIFIED")
    safety = number(p["safety"])
    gross = finite(mark["gross"])
    fund = finite(mark["funding"])
    net = finite(mark["net"])
    if (
        any(isinstance(mark[k], bool) for k in ("gross", "funding", "net"))
        or not all(math.isfinite(x) for x in (gross, fund, net))
        or not math.isclose(
            number(mark["entry_fees"]), p["entry_fees"], rel_tol=1e-8, abs_tol=1e-10
        )
        or number(mark["safety"]) != safety
        or not math.isclose(
            net,
            gross - p["entry_fees"] - number(mark["exit_fees"]) - safety + fund,
            rel_tol=1e-8,
            abs_tol=1e-10,
        )
        or not math.isclose(finite(p["net"]), net, rel_tol=1e-8, abs_tol=1e-10)
    ):
        raise ValueError("FUNDING_CLOSE_LINEAGE_UNVERIFIED")
    return dict(
        id=p["id"],
        symbol=p["symbol"],
        buy=p["buy"],
        sell=p["sell"],
        opened_at=opened,
        closed_at=closed,
        base_qty=qty,
        safety_usd=safety,
    ), {p["buy"]: fee_rate, p["sell"]: fee_rate}


def simulate(position, books, rates, scenario):
    p, fee_rates = normalize(position)
    result = execute(p, books, scenario, fee_rates, recovery_rounds=3)
    result.update(
        mode=MODE,
        strategy="funding_arb",
        execution_status=result["status"],
        net_without_funding=result["net"],
        funding=None,
        funding_mode="PUBLIC_SETTLED_RATE_ENTRY_VWAP_REFERENCE_MODEL",
        funding_known=False,
        funding_reason="EXPOSURE_NOT_FLAT",
        funding_events=[],
        rate_windows=[],
    )
    filled = [o for o in result["orders"] if o["filled"] > 0]
    if not filled:
        result.update(funding=0.0, funding_known=True, funding_reason="NO_EXPOSURE")
    elif result["status"] != "RESIDUAL_EXPOSURE":
        events, windows = [], []
        try:
            for key, prefix in (("buy", "long"), ("sell", "short")):
                venue = p[key]
                orders = sorted(
                    (o for o in filled if o["venue"] == venue),
                    key=lambda o: o["arrival"],
                )
                if not orders:
                    continue
                start, end = orders[0]["arrival"], orders[-1]["arrival"]
                first = number(position[prefix + "_next"], True)
                period = number(position[prefix + "_interval"], True) * 3600
                window = rates.covering(venue, p["symbol"], start, end, first, period)
                if any(
                    o["book_evidence"]["instrument"] != window["instrument"]
                    for o in orders
                ):
                    raise ValueError("FUNDING_BOOK_IDENTITY_CONFLICT")
                entry = [o for o in orders if o["phase"] == "ENTRY"]
                reference = math.fsum(
                    o["filled"] * o["price"] for o in entry
                ) / math.fsum(o["filled"] for o in entry)
                windows.append(window)
                for event in window["events"]:
                    stamp = event["reported_ts"]
                    if not start <= stamp <= end:
                        continue
                    if any(o["arrival"] == stamp for o in orders):
                        raise ValueError("FUNDING_ORDER_BOUNDARY_AMBIGUOUS")
                    exposure = math.fsum(
                        o["filled"] * (1 if o["side"] == "BUY" else -1)
                        for o in orders
                        if o["arrival"] < stamp
                    )
                    amount = -exposure * event["rate"] * reference
                    if not math.isfinite(amount):
                        raise ValueError("FUNDING_MODEL_OVERFLOW")
                    events.append(
                        dict(
                            venue=venue,
                            ts=stamp,
                            calendar_ts=event["ts"],
                            rate=event["rate"],
                            base_exposure=exposure,
                            entry_reference=reference,
                            amount=amount,
                            mode=result["funding_mode"],
                        )
                    )
            funding = math.fsum(e["amount"] for e in events)
            net = result["net_without_funding"] + funding
            if not math.isfinite(net):
                raise ValueError("FUNDING_MODEL_OVERFLOW")
        except (
            ValueError,
            KeyError,
            TypeError,
            ZeroDivisionError,
            OverflowError,
        ) as error:
            result.update(
                status="FUNDING_ACCOUNTING_UNKNOWN",
                net=None,
                funding_reason=(
                    str(error)
                    if isinstance(error, ValueError)
                    else "FUNDING_EVIDENCE_INVALID"
                ),
            )
        else:
            result.update(
                net=net,
                funding=funding,
                funding_known=True,
                funding_reason="VERIFIED_PUBLIC_MODEL_WINDOW",
                funding_events=events,
                rate_windows=windows,
            )
    if result["net"] is not None:
        # Execution delta explains the difference to the original Paper basis;
        # it is already inside modeled cashflow and is never deducted twice.
        comparable = result["execution_status"] in ("CLOSED", "CLOSED_WITH_RECOVERY")
        reference = float(position["last_mark"]["gross"]) if comparable else None
        result["attribution"] = dict(
            basis=result["cashflow"],
            fees=result["fees"],
            funding=result["funding"],
            safety=result["safety"],
            reference_paper_basis=reference,
            adverse_execution_delta=(
                reference - result["cashflow"] if comparable else None
            ),
            net=result["net"],
            slippage_mode="EXPLAIN_ONLY_ALREADY_IN_BASIS",
        )
    else:
        result["attribution"] = None
    return result


async def build(path, scenarios=SCENARIOS, limit=100, persist=True, clock=time.time):
    scenarios = tuple(scenarios)
    if (
        not scenarios
        or len({s.name for s in scenarios}) != len(scenarios)
        or isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 0 < limit <= 1000
    ):
        raise ValueError("FUNDING_REPLAY_PARAMETERS_INVALID")
    created = number(clock())
    report = dict(
        mode=MODE,
        created_at=created,
        release_authorized=False,
        sample_size=0,
        excluded={},
        scenarios=[],
        status="HISTORY_NOT_COLLECTED",
        assumptions=[
            "Public settled rates valued at modeled entry VWAP; not private income or mark-price valuation",
            "Only verified calendar coverage, mature history and exposure actually held at settlement",
            "Funding at the exact virtual fill timestamp is ambiguous and blocks final NET",
            "Independent position replay, IOC/taker, fixed Paper quote-unit fees, three bounded recovery rounds",
            "Public depth is consumed once per snapshot-side; no queue/native/account certification or Ledger credit",
            "Original Paper times, no rule selection; observed history is retrospective evidence, not an entry forecast",
        ],
    )
    excluded, positions, results = Counter(), [], []
    async with aiosqlite.connect(f"file:{path}?mode=ro", uri=True) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN")
        c = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0] for r in await c.fetchall()}
        if {"funding_paper", "market_books"} <= tables:
            c = await db.execute(
                "SELECT * FROM funding_paper WHERE status='CLOSED' ORDER BY opened_at DESC,id DESC LIMIT ?",
                (limit,),
            )
            for row in await c.fetchall():
                try:
                    p = json.loads(row["payload"])
                    if any(
                        p[k] != row[k]
                        for k in (
                            "id",
                            "symbol",
                            "buy",
                            "sell",
                            "opened_at",
                            "closed_at",
                            "status",
                            "net",
                        )
                    ):
                        raise ValueError("FUNDING_POSITION_IDENTITY_CONFLICT")
                    normalize(p)
                    positions.append(p)
                except (ValueError, KeyError, TypeError, OverflowError, AttributeError):
                    excluded["POSITION_OR_ACCOUNTING_UNVERIFIED"] += 1
            if positions:
                start = min(p["opened_at"] for p in positions) - max(
                    s.max_age for s in scenarios
                )
                end = max(p["closed_at"] for p in positions) + max(
                    max(s.long_latency, s.short_latency) + 3 * s.recovery_latency
                    for s in scenarios
                )
                c = await db.execute(
                    "SELECT payload FROM market_books WHERE received_at BETWEEN ? AND ? ORDER BY received_at,id LIMIT 200001",
                    (start, end),
                )
                book_rows = await c.fetchall()
                rate_rows = []
                if "funding_rate_windows" in tables:
                    c = await db.execute(
                        "SELECT payload FROM funding_rate_windows WHERE opened_at<=? AND covered_until>=? AND observed_at<=? ORDER BY id LIMIT 20001",
                        (end, start, created),
                    )
                    rate_rows = await c.fetchall()
                if len(book_rows) > 200000 or len(rate_rows) > 20000:
                    report["status"] = "HISTORY_TOO_LARGE"
                else:
                    try:
                        books = BookTape([json.loads(r[0]) for r in book_rows])
                        rates = RateTape([json.loads(r[0]) for r in rate_rows], created)
                    except (ValueError, KeyError, TypeError, AttributeError):
                        report["status"] = "HISTORY_INVALID"
                    else:
                        for p in positions:
                            try:
                                batch = [
                                    dict(
                                        simulate(p, books, rates, s),
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
                        report["status"] = "MODELED" if results else "INSUFFICIENT_DATA"
            else:
                report["status"] = "INSUFFICIENT_DATA"
    report.update(
        excluded=dict(excluded), sample_size=len({r["position_id"] for r in results})
    )
    for scenario in scenarios:
        rows = [r for r in results if r["scenario"] == scenario.name]
        nets = [r["net"] for r in rows if r["net"] is not None]
        report["scenarios"].append(
            dict(
                parameters=asdict(scenario),
                statuses=dict(Counter(r["status"] for r in rows)),
                completed=len(nets),
                net=math.fsum(nets) if nets else None,
                fees=math.fsum(r["fees"] for r in rows),
                funding=math.fsum(r["funding"] for r in rows if r["funding_known"]),
                funding_unknown=sum(not r["funding_known"] for r in rows),
                residual_positions=sum(
                    r["execution_status"] == "RESIDUAL_EXPOSURE" for r in rows
                ),
            )
        )
    if persist:
        async with aiosqlite.connect(path) as db:
            await db.executescript(SCHEMA)
            await db.execute("BEGIN IMMEDIATE")
            c = await db.execute(
                "INSERT INTO funding_execution_runs(created_at,payload) VALUES(?,?)",
                (created, json.dumps(report, allow_nan=False)),
            )
            for r in results:
                await db.execute(
                    "INSERT INTO funding_execution_results(run_id,position_id,scenario,status,net,payload) VALUES(?,?,?,?,?,?)",
                    (
                        c.lastrowid,
                        r["position_id"],
                        r["scenario"],
                        r["status"],
                        r["net"],
                        json.dumps(r, allow_nan=False),
                    ),
                )
            await db.commit()
        report["run_id"] = c.lastrowid
    return report


def render(report):
    labels = dict(
        STATUS_LABELS,
        FUNDING_ACCOUNTING_UNKNOWN="funding не подтверждён модельной историей",
        HISTORY_INVALID="история повреждена",
        HISTORY_TOO_LARGE="выборка превышает лимит",
    )
    out = [
        "🧪 <b>Funding · исполнение и начисления</b>",
        "<i>IOC по записанным стаканам; публичные settled rates по объёму открытой ноги.</i>",
        f"Сделок: {report['sample_size']} · исключено: {sum(report['excluded'].values())}",
    ]
    if report["status"] != "MODELED":
        out.append(escape(labels.get(report["status"], report["status"])))
    for s in report["scenarios"]:
        name = s["parameters"]["name"]
        net = "не определён" if s["net"] is None else f"{s['net']:+.4f} USD"
        out.append(
            f"\n<b>{escape(SCENARIO_LABELS.get(name, name))}</b> · завершено: {s['completed']} · NET: {net}\nОстатков: {s['residual_positions']} · funding неизвестен: {s['funding_unknown']}\nПодтверждённый модельный funding: {s['funding']:+.4f} USD"
        )
        out.append(
            "Статусы: "
            + escape(
                ", ".join(f"{labels.get(k,k)}: {v}" for k, v in s["statuses"].items())
                or "нет"
            )
        )
    out.append(
        "\n<i>Valuation по entry VWAP, а не фактической mark price. Публичная ставка не является доходом аккаунта. Комиссии модельные; капитал и допуск к LIVE не меняются.</i>"
    )
    return "\n".join(out)
