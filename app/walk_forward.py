"""Expanding-window, purged, disjoint holdout research for all five strategies.

Each fold selects rules using completed training windows only. Selection cannot
see its test marks. No rule promotion, LIVE authority or realized-account credit.
"""

import json
import asyncio
import math
from itertools import groupby
from pathlib import Path

import aiosqlite

from .spot_future_history_replay import dataset, evaluate as split_evaluate, simulate
from .spot_future_replay import metrics

STRATEGIES = ("futures_futures", "spot_futures", "spot_spot", "funding_arb", "cex_dex")
LABELS = dict(
    zip(
        STRATEGIES,
        (
            "Фьючерсы ↔ Фьючерсы",
            "Спот ↔ Фьючерсы",
            "Спот ↔ Спот",
            "Funding",
            "CEX ↔ DEX",
        ),
    )
)


def json_metrics(value):
    if value is None:
        return None
    result = dict(value)
    if result.get("profit_factor") == math.inf:
        result.update(profit_factor=None, profit_factor_unbounded=True)
    return result


def evaluate(
    trades,
    excluded=None,
    *,
    strategy="spot_futures",
    initial_train=20,
    test_size=10,
    min_train=10,
    max_folds=20,
):
    if (
        strategy not in STRATEGIES
        or any(
            type(x) is not int or x < 1
            for x in (initial_train, test_size, min_train, max_folds)
        )
        or initial_train < min_train
        or max_folds > 100
    ):
        raise ValueError("WALK_FORWARD_CONFIGURATION_INVALID")
    report = dict(
        mode="PURGED_EXPANDING_WALK_FORWARD_MODEL",
        strategy=strategy,
        status="INSUFFICIENT_DATA",
        eligible=len(trades),
        excluded=excluded or {},
        folds=[],
        execution_authority=False,
        release_authorized=False,
        model_positive=False,
        capped=False,
        settings=dict(
            initial_train=initial_train,
            test_size=test_size,
            min_train=min_train,
            max_folds=max_folds,
        ),
    )
    ordered = sorted(trades, key=lambda x: (x["opened_at"], x["id"]))
    if len({x["id"] for x in ordered}) != len(ordered):
        raise ValueError("WALK_FORWARD_DUPLICATE_POSITION")
    if any(
        not math.isfinite(x["opened_at"])
        or not math.isfinite(x["closed_at"])
        or x["closed_at"] < x["opened_at"]
        for x in ordered
    ):
        raise ValueError("WALK_FORWARD_WINDOW_INVALID")
    # Never split a group of positions that opened at the same instant.
    groups = [list(rows) for _, rows in groupby(ordered, key=lambda x: x["opened_at"])]
    train, index, exits, seen_test = [], 0, [], set()
    while index < len(groups) and len(train) < initial_train:
        train.extend(groups[index])
        index += 1
    while index < len(groups):
        if len(report["folds"]) >= max_folds:
            report["capped"] = True
            break
        test = []
        while index < len(groups) and len(test) < test_size:
            test.extend(groups[index])
            index += 1
        boundary = test[0]["opened_at"]
        purged_train = [x for x in train if x["closed_at"] < boundary]
        fold = dict(
            index=len(report["folds"]) + 1,
            train_candidates=len(train),
            train_size=len(purged_train),
            purged=len(train) - len(purged_train),
            test_size=len(test),
            test_ids=[x["id"] for x in test],
            test_start=boundary,
            test_end=max(x["closed_at"] for x in test),
            status="INSUFFICIENT_TRAIN",
            model_positive=False,
        )
        if len(purged_train) >= min_train:
            # Existing selector uses only train marks; force the exact desired cut.
            fitted = split_evaluate(
                purged_train + test,
                strategy=strategy,
                min_train=min_train,
                min_test=len(test),
                train_ratio=(len(purged_train) + 0.5) / (len(purged_train) + len(test)),
            )
            fold.update(
                status=fitted["status"],
                model_positive=fitted.get("model_positive", False),
            )
            if "parameters" in fitted:
                params = fitted["parameters"]
                tested = simulate(
                    test,
                    params["seconds"],
                    params["trailing"],
                    params.get("target"),
                    include_exits=True,
                )
                fold.update(
                    parameters=params,
                    train_metrics=json_metrics(fitted["train"]["metrics"]),
                    test_metrics=json_metrics(tested["metrics"]),
                    censored=tested["censored"],
                )
                exits.extend(tested["exits"])
        seen_test.update(fold["test_ids"])
        report["folds"].append(fold)
        # Previously tested trades become eligible training only in later folds
        # and only after their original position close precedes that fold boundary.
        train.extend(test)
    exits.sort()
    totals = metrics([x[2] for x in exits])
    if totals:
        equity = peak = drawdown = 0.0
        for _, same_time in groupby(exits, key=lambda x: x[0]):
            equity += math.fsum(x[2] for x in same_time)
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
        totals["max_drawdown"] = drawdown
    complete = bool(report["folds"]) and all(
        f["status"] == "VALIDATED_SPLIT" for f in report["folds"]
    )
    report.update(
        status=(
            "MULTI_FOLD_VALIDATED_MODEL"
            if complete and len(report["folds"]) >= 2 and not report["capped"]
            else "PARTIAL_FOLDS" if report["folds"] else "INSUFFICIENT_DATA"
        ),
        tested_positions=len(seen_test),
        completed_positions=len(exits),
        aggregate_test_metrics=json_metrics(totals),
        positive_folds=sum(f["model_positive"] for f in report["folds"]),
    )
    report["model_positive"] = bool(
        report["status"] == "MULTI_FOLD_VALIDATED_MODEL"
        and all(f["model_positive"] for f in report["folds"])
    )
    return report


async def read(db, max_gap=120, **settings):
    reports = {}
    async with db.execute("SELECT name FROM sqlite_master WHERE type='table'") as c:
        tables = {r[0] for r in await c.fetchall()}
    trade_tables = dict(
        zip(
            STRATEGIES,
            (
                "paper_positions",
                "spot_future_paper",
                "spot_spot_paper",
                "funding_paper",
                "cex_dex_paper",
            ),
        )
    )
    for strategy in STRATEGIES:
        trades, excluded = (
            await dataset(
                None,
                max_gap,
                strategy,
                connection=db,
                limit=10000,
                mark_limit=10000,
                total_mark_limit=500000,
            )
            if trade_tables[strategy] in tables
            else ([], {"HISTORY_NOT_COLLECTED": 1})
        )
        reports[strategy] = await asyncio.to_thread(
            evaluate, trades, excluded, strategy=strategy, **settings
        )
    return dict(
        mode="READ_ONLY_WALK_FORWARD", execution_authority=False, strategies=reports
    )


async def build(path, max_gap=120, **settings):
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError("WALK_FORWARD_DATABASE_MISSING")
    async with aiosqlite.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        await db.execute("BEGIN")
        result = await read(db, max_gap, **settings)
        await db.rollback()
    return result


def render(report):
    out = [
        "🔬 <b>Walk-forward · пять стратегий</b>",
        "Правила выбираются до каждого отдельного периода проверки. Пересекающиеся train-окна исключаются. Это модель, не разрешение LIVE.",
    ]
    for strategy, row in report["strategies"].items():
        out.append(
            f"\n<b>{LABELS[strategy]}</b> · {row['status']}\nПригодных {row['eligible']} · периодов {len(row['folds'])} · положительных {row.get('positive_folds', 0)}"
        )
        m = row.get("aggregate_test_metrics")
        if m:
            out.append(
                f"NET проверки {m['net']:+.4f} USD · просадка {m['max_drawdown']:.4f} · завершено {row['completed_positions']}/{row['tested_positions']}"
            )
        if row["excluded"]:
            out.append(f"Исключений истории: {sum(row['excluded'].values())}")
        if row["capped"]:
            out.append("Достигнут лимит периодов: отчёт неполный.")
    out.append(
        "\nНезавершённые проверки не считаются успешными. Параметры не меняются; полные периоды — в экспорте."
    )
    return "\n".join(out)


def main():
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--html", action="store_true")
    args = parser.parse_args()
    try:
        report = asyncio.run(build(args.db))
    except (FileNotFoundError, ValueError, aiosqlite.Error) as error:
        parser.exit(2, f"Walk-forward unavailable: {error}\n")
    print(
        render(report)
        if args.html
        else json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    )


if __name__ == "__main__":
    main()
