"""Chronological, purged replay of saved model NET marks; never enables LIVE.
Legacy scalar marks cannot prove quantity/cost lineage and are excluded.
"""

import json
import math
import aiosqlite
from .spot_future_exit import decide
from .spot_future_replay import metrics


async def dataset(path, max_gap=120, strategy="spot_futures"):
    if strategy not in ("spot_futures", "spot_spot", "funding_arb"):
        raise ValueError("REPLAY_STRATEGY_INVALID")
    trade_table = {
        "spot_futures": "spot_future_paper",
        "spot_spot": "spot_spot_paper",
        "funding_arb": "funding_paper",
    }[strategy]
    mark_table = {
        "spot_futures": "spot_future_marks",
        "spot_spot": "spot_spot_marks",
        "funding_arb": "funding_paper_marks",
    }[strategy]
    trades = []
    excluded = {}

    def reject(reason):
        excluded[reason] = excluded.get(reason, 0) + 1

    async with aiosqlite.connect(path) as d:
        d.row_factory = aiosqlite.Row
        await d.execute("BEGIN")
        async with d.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (mark_table,),
        ) as c:
            if not await c.fetchone():
                return [], {"HISTORY_NOT_COLLECTED": 1}
        async with d.execute("PRAGMA table_info(" + mark_table + ")") as c:
            if "payload" not in {x["name"] for x in await c.fetchall()}:
                return [], {"LEGACY_MARKS": 1}
        async with d.execute(
            "SELECT * FROM "
            + trade_table
            + (
                " WHERE status='CLOSED' ORDER BY opened_at,id"
                if strategy == "funding_arb"
                else " WHERE status!='OPEN' ORDER BY opened_at,id"
            )
        ) as c:
            rows = await c.fetchall()
        for row in rows:
            async with d.execute(
                "SELECT * FROM " + mark_table + " WHERE position_id=? ORDER BY ts,id",
                (row["id"],),
            ) as c:
                marks = await c.fetchall()
            if not marks:
                reject("MARKS_MISSING")
                continue
            try:
                position = json.loads(row["payload"])
                opened = float(row["opened_at"])
                closed = float(row["closed_at"])
                qty = float(position["base_qty"])
                if (
                    not all(math.isfinite(x) for x in (opened, closed, qty))
                    or qty <= 0
                    or closed < opened
                ):
                    raise ValueError("WINDOW_INVALID")
                if position.get("entry_fee_pct") is None:
                    raise ValueError("LEGACY_ENTRY_COSTS")
                path_marks = []
                previous = opened
                for mark in marks:
                    if not mark["payload"]:
                        raise ValueError("LEGACY_MARKS")
                    p = json.loads(mark["payload"])
                    ts = float(mark["ts"])
                    net = float(mark["net"])
                    if strategy == "funding_arb" and not p.get("funding_known"):
                        continue
                    if p.get("mode") not in (
                        "PAPER_MODEL",
                        "FUNDING_PUBLIC_HISTORY_MODEL",
                    ):
                        raise ValueError("MARK_MODE_INVALID")
                    if not all(
                        math.isfinite(float(p[k]))
                        for k in (
                            "net",
                            "gross",
                            "entry_fees",
                            "exit_fees",
                            "safety",
                            "funding",
                            "base_qty",
                        )
                    ):
                        raise ValueError("MARK_INVALID")
                    if (
                        not math.isfinite(ts)
                        or not math.isfinite(net)
                        or not math.isclose(float(p["base_qty"]), qty, rel_tol=1e-8)
                    ):
                        raise ValueError("QUANTITY_INVALID")
                    if not math.isclose(
                        net, float(p["net"]), abs_tol=1e-8
                    ) or not math.isclose(
                        net,
                        p["gross"]
                        - p["entry_fees"]
                        - p["exit_fees"]
                        - p["safety"]
                        + p["funding"],
                        abs_tol=1e-8,
                    ):
                        raise ValueError("COST_LINEAGE_INVALID")
                    if (
                        ts < previous
                        or ts < opened
                        or ts > closed
                        or ts - previous > max_gap
                    ):
                        raise ValueError("MARK_GAP_OR_WINDOW_INVALID")
                    if ts == previous and path_marks:
                        if not math.isclose(path_marks[-1][1], net, abs_tol=1e-8):
                            raise ValueError("DUPLICATE_MARK_CONFLICT")
                        continue
                    path_marks.append((ts, net))
                    previous = ts
                if not path_marks:
                    raise ValueError("VERIFIED_MARKS_MISSING")
                if closed - previous > max_gap:
                    raise ValueError("END_GAP")
                trades.append(
                    {
                        "id": row["id"],
                        "opened_at": opened,
                        "closed_at": closed,
                        "marks": path_marks,
                    }
                )
            except (ValueError, TypeError, KeyError) as e:
                reject(str(e) if isinstance(e, ValueError) else "PAYLOAD_INVALID")
    return trades, excluded


def simulate(trades, seconds, trailing):
    nets = []
    censored = 0
    for trade in trades:
        best = 0
        value = None
        for ts, net in trade["marks"]:
            best = max(best, net)
            if decide(net, best, ts - trade["opened_at"], seconds, trailing).close:
                value = net
                break
        if value is None:
            censored += 1
        else:
            nets.append(value)
    return {"metrics": metrics(nets), "censored": censored}


def evaluate(trades, excluded=None, min_train=3, min_test=3):
    report = {
        "status": "INSUFFICIENT_DATA",
        "eligible": len(trades),
        "excluded": excluded or {},
        "release_authorized": False,
        "mode": "PAPER_MARK_REPLAY",
        "purged": 0,
    }
    ordered = sorted(trades, key=lambda x: (x["opened_at"], x["id"]))
    if len(ordered) < min_train + min_test:
        return report
    cut = int(len(ordered) * 0.7)
    cut = min(cut, len(ordered) - min_test)
    test = ordered[cut:]
    boundary = test[0]["opened_at"]
    train = [x for x in ordered[:cut] if x["closed_at"] < boundary]
    report.update(train_size=len(train), test_size=len(test), purged=cut - len(train))
    if len(train) < min_train:
        return report
    candidates = []
    for seconds in (120, 300, 600, 1200):
        for trailing in (0.1, 0.2, 0.3):
            result = simulate(train, seconds, trailing)
            if result["censored"] == 0 and result["metrics"]:
                candidates.append(
                    (
                        result["metrics"]["net"],
                        -result["metrics"]["max_drawdown"],
                        seconds,
                        trailing,
                        result,
                    )
                )
    if not candidates:
        report["status"] = "CENSORED_TRAIN"
        return report
    _, _, seconds, trailing, trained = max(
        candidates, key=lambda x: (x[0], x[1], -x[2], -x[3])
    )
    tested = simulate(test, seconds, trailing)
    report.update(
        status="CENSORED_TEST" if tested["censored"] else "VALIDATED_SPLIT",
        parameters={"seconds": seconds, "trailing": trailing},
        train=trained,
        test=tested,
    )
    # Describes model holdout quality only, never a release or production promotion.
    m = tested["metrics"]
    report["model_positive"] = bool(
        not tested["censored"] and m and m["net"] > 0 and m["profit_factor"] >= 1.05
    )
    return report


async def build(path, max_gap=120, strategy="spot_futures"):
    trades, excluded = await dataset(path, max_gap, strategy)
    report = evaluate(trades, excluded)
    report["strategy"] = strategy
    return report


def render(report):
    out = [
        "🧪 <b>Проверка истории · "
        + (
            "Funding · модель истории ставок"
            if report.get("strategy") == "funding_arb"
            else (
                "Спот ↔ Спот"
                if report.get("strategy") == "spot_spot"
                else "Спот ↔ Фьючерсы"
            )
        )
        + "</b>",
        "<i>Модель по записанным ценам и затратам Paper.</i>",
        f"\nПодходящих сделок: <b>{report['eligible']}</b>",
        f"Исключено: <b>{sum(report['excluded'].values())}</b> · пересечение окон: <b>{report['purged']}</b>",
    ]
    status = report["status"]
    if status == "INSUFFICIENT_DATA":
        out.append(
            "\nНужны минимум 3 сделки для обучения и 3 для отдельной проверки. Старые marks без состава затрат не используются."
        )
    elif status == "CENSORED_TRAIN":
        out.append(
            "\nНедостаточная длина историй: ни один набор правил не завершил все обучающие сделки."
        )
    else:
        p = report["parameters"]
        tr = report["train"]["metrics"]
        te = report["test"]["metrics"]
        out.append(
            f"\nПравила: {p['seconds']} сек · откат NET {p['trailing']:.0%}\nОбучение: {report['train_size']} · NET {tr['net']:+.4f} USD\nОтдельная проверка: {report['test_size']} · без выхода: {report['test']['censored']}"
        )
        if te:
            out.append(
                f"NET проверки: <b>{te['net']:+.4f} USD</b> · PF {te['profit_factor']:.2f}\nМаксимальная просадка: {te['max_drawdown']:.4f} USD"
            )
        if status == "CENSORED_TEST":
            out.append(
                "Проверка неполная: часть историй закончилась до сигнала выхода."
            )
    out.append(
        "\n<i>Параметры не меняются автоматически. Задержки и реальные fills здесь не симулируются.</i>"
    )
    return "\n".join(out)
