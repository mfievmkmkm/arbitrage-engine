"""Read-only delivery audit. Never changes LIVE gates or treats test count as release."""

import time
import math
from pathlib import Path
from urllib.parse import quote
from html import escape
import aiosqlite
from .live_acceptance import accepted
from .spot_future_history_replay import dataset, evaluate
from .live_execution_costs import read as read_costs
from .readiness_evidence import stress, inventory, digest, finite

TABLES = {
    "futures_futures": "paper_positions",
    "spot_futures": "spot_future_paper",
    "spot_spot": "spot_spot_paper",
    "funding_arb": "funding_paper",
    "cex_dex": "cex_dex_paper",
}


def json_metrics(metrics):
    """No-loss PF is unbounded, not an invalid price or a JSON Infinity."""
    if metrics is None:
        return None
    result = dict(metrics)
    if result.get("profit_factor") == math.inf:
        result.update(profit_factor=None, profit_factor_unbounded=True)
    return result


async def read(d, now):
    """Use caller's read transaction for every database-derived observation."""
    counts, active, wallet_pending = {}, 0, 0
    d.row_factory = aiosqlite.Row
    async with d.execute("SELECT name FROM sqlite_master WHERE type='table'") as c:
        tables = {r[0] for r in await c.fetchall()}
    for strategy, table in TABLES.items():
        if table in tables:
            async with d.execute(
                "SELECT COUNT(*) FROM "
                + table
                + (
                    " WHERE status!='OPEN' AND closed_at IS NOT NULL"
                    if strategy in ("spot_futures", "spot_spot")
                    else " WHERE status='CLOSED'"
                )
            ) as c:
                counts[strategy] = (await c.fetchone())[0]
        else:
            counts[strategy] = 0
    if "live_trades" in tables:
        async with d.execute(
            "SELECT COUNT(*) FROM live_trades WHERE phase NOT IN ('CLOSED_PRIVATE_VERIFIED','CLOSED_WITH_INVENTORY','ABORTED')"
        ) as c:
            active = (await c.fetchone())[0]
    if "wallet_tx_intents" in tables:
        async with d.execute(
            "SELECT COUNT(*) FROM wallet_tx_intents WHERE phase NOT IN ('FINALIZED_SUCCESS','FINALIZED_REVERT')"
        ) as c:
            wallet_pending = (await c.fetchone())[0]
    sample, stresses = {}, {}
    for strategy, count in counts.items():
        trades, excluded = (
            await dataset(
                None, strategy=strategy, connection=d, limit=10000, mark_limit=10000
            )
            if count
            else ([], {})
        )
        report = evaluate(trades, excluded, strategy=strategy) if count else None
        sample[strategy] = dict(
            closed=count,
            eligible=report["eligible"] if report else 0,
            oos_model_positive=bool(report and report.get("model_positive")),
            reason=(
                report.get("status", "MODEL_REVIEW_ONLY")
                if report
                else "HISTORY_MISSING"
            ),
            excluded=report["excluded"] if report else {},
            purged=report["purged"] if report else 0,
            train_size=report.get("train_size", 0) if report else 0,
            test_size=report.get("test_size", 0) if report else 0,
            test_metrics=(
                json_metrics(report.get("test", {}).get("metrics")) if report else None
            ),
        )
        stresses[strategy] = await stress(d, tables, strategy, now)
    costs = await read_costs(d, limit=1000)
    held_inventory = 0
    if "live_cash_inventory" in tables:
        async with d.execute(
            "SELECT COUNT(*) FROM live_cash_inventory WHERE qty!=0"
        ) as c:
            held_inventory = (await c.fetchone())[0]
    return dict(
        samples=sample,
        execution_stress=stresses,
        actual_costs=costs,
        active_live=active,
        wallet_pending=wallet_pending,
        held_inventory_records=held_inventory,
    )


async def build(
    path, acceptance_path, venues=(), now=None, dex_runtime_connected=False
):
    now = finite(time.time() if now is None else now)
    database = Path(path).resolve()
    if not database.is_file():
        raise FileNotFoundError("READINESS_DATABASE_MISSING")
    if now < 0:
        raise ValueError("READINESS_TIME_INVALID")
    uri = "file:" + quote(str(database), safe="/") + "?mode=ro"
    async with aiosqlite.connect(uri, uri=True) as d:
        await d.execute("BEGIN")
        evidence = await read(d, now)
        await d.rollback()
    active, wallet_pending = evidence["active_live"], evidence["wallet_pending"]
    account = {}
    for strategy in ("futures_futures", "spot_futures", "spot_spot", "funding_arb"):
        account[strategy] = bool(venues) and (
            all(accepted(acceptance_path, (v,), now, strategy=strategy) for v in venues)
            if strategy == "spot_futures"
            else accepted(
                acceptance_path,
                tuple(venues),
                now,
                strategy=None if strategy == "futures_futures" else strategy,
            )
        )
    missing = [
        *(
            []
            if dex_runtime_connected is True
            else ["DEX_LIVE_RUNTIME_CONFIGURATION_REQUIRED"]
        ),
        "REAL_ACCOUNT_AND_WALLET_CERTIFICATION",
        "LONG_RUNNING_PAPER_OOS_AND_MICRO_CANARY",
    ]
    if active or wallet_pending:
        missing.append("UNRESOLVED_LIVE_OR_WALLET_EXPOSURE")
    result = dict(
        mode="READ_ONLY_PROJECT_AUDIT",
        software_complete=False,
        dex_software_components_complete=True,
        dex_runtime_connected=dex_runtime_connected is True,
        production_ready=False,
        missing=missing,
        **evidence,
        generated_at=now,
        snapshot_scope="SINGLE_SQLITE_READ_TRANSACTION",
        evidence_inventory=inventory(
            evidence["samples"],
            evidence["execution_stress"],
            evidence["actual_costs"],
            account,
        ),
        account_acceptance=account,
        implemented=[
            "CEX_FF_SF_SS_FUNDING_LIFECYCLES",
            "TWO_SIDED_DEX_PAPER_REPLAY",
            "DEX_SEQUENTIAL_EXECUTION_STRESS",
            "ISOLATED_WALLET_SIGNING_AND_NONCE_JOURNAL",
            "DUAL_RPC_FINALIZED_RECEIPT_CASHFLOW",
            "DEX_DURABLE_BRIDGE_SESSION_AND_CEX_BACKEND",
            "DEX_READ_ONLY_BRIDGE_RUNTIME_OBSERVER",
            "DEX_CONFIGURED_AUTO_ENTRY_AND_PAIRED_NET_EXIT",
            "DEX_RESTART_SAFE_BOUNDED_RECOVERY_AND_FINAL_ACCOUNTING",
            "BOUNDED_PUBLIC_BOOK_PRIORITIES_AND_ACKNOWLEDGED_ROTATION",
            "SECONDARY_SPOT_AND_LINEAR_REST_WS_BASE_UNIT_TAPE",
            "SEQUENTIAL_SF_SS_RECORDED_IOC_STRESS_AND_INVENTORY_PROOF",
            "FUNDING_RECORDED_IOC_SETTLEMENT_EXPOSURE_AND_PUBLIC_RATE_WINDOWS",
            "TELEGRAM_DIARY_EXPORT",
        ],
        live_allowed=False,
    )
    result["evidence_sha256"] = digest(result)
    return result


def render(report):
    labels = {
        "futures_futures": "Фьючерсы ↔ Фьючерсы",
        "spot_futures": "Спот ↔ Фьючерсы",
        "spot_spot": "Спот ↔ Спот",
        "funding_arb": "Funding",
        "cex_dex": "CEX ↔ DEX",
    }
    out = [
        "🏁 <b>Проверка готовности проекта</b>",
        "<i>Отчёт не разрешает торговлю и не меняет настройки. Прохождение тестов не является проверкой аккаунта.</i>",
        "\n<b>Реализовано</b>\nCEX lifecycles, DEX Paper/Replay, stress задержек/частичных fills, wallet signer/nonce journal, dual-RPC receipts, durable bridge session/CEX backend, дневник и экспорт.",
        "\n<b>До допуска к реальной торговле</b>\n• Настроить DEX runtime: изолированный кошелёк, два RPC, token registry, маршруты и лимиты. Автоматические входы, shared monitor, динамические NET-выходы и bounded recovery реализованы.\n• Проверить выбранные аккаунты, кошелёк и scope разрешений.\n• Собрать длительную Paper/OOS-историю, затем отдельно провести micro-canary.",
        f"\nНезавершённых LIVE: {report['active_live']} · wallet intents: {report['wallet_pending']}",
        f"Остатки спот-инвентаря: {report.get('held_inventory_records', 0)} записей · не private-flat и не оценка прибыли.",
        "\n<b>Сохранённая модельная история</b>",
    ]
    for strategy, row in report["samples"].items():
        out.append(
            f"{labels[strategy]}: CLOSED {row['closed']} · проверяемых {row['eligible']} · OOS модели {'положительный' if row['oos_model_positive'] else 'не подтверждён'}"
        )
    out.append("\n<b>Account acceptance</b>")
    for strategy, ok in report["account_acceptance"].items():
        out.append(
            labels[strategy]
            + ": "
            + (
                "действует операторское evidence"
                if ok
                else "не подтверждён для выбранного scope"
            )
        )
    out.append(
        "\nСтатус: не завершён и не сертифицирован для production. Исходные материалы и тестовые fixtures не подменяют эту проверку."
    )
    out.append("\n<b>Исполнение и фактические расходы</b>")
    for row in report.get("evidence_inventory", []):
        model = report["execution_stress"][row["strategy"]]
        out.append(
            f"{labels[row['strategy']]}: stress {escape(model['status'])} · сверено циклов {row['reconciled_cycles_in_sample']}/{row['actual_cycles_in_sample']}"
        )
        if model.get("incomplete_results"):
            out.append(
                f"  Неопределённых исходов модели: {model['incomplete_results']}"
            )
        if model.get("stale"):
            out.append(
                "  Последний stress старше 24 часов; это диагностический порог, не допуск."
            )
        if row["diagnostic_gaps"]:
            from .readiness_evidence import GAP_LABELS

            out.append(
                "  Проверить: "
                + escape(
                    ", ".join(
                        GAP_LABELS.get(x, "stress: нет пригодного evidence")
                        for x in row["diagnostic_gaps"]
                    )
                )
            )
    if report.get("evidence_sha256"):
        out.append(
            "\nСнимок evidence: <code>"
            + report["evidence_sha256"][:16]
            + "</code> · одна транзакция базы."
        )
    out.append(
        "NET разных stress-сценариев не складывается: это повторные модели одних сделок. Paper/OOS и сверенные расходы не заменяют micro-canary."
    )
    return "\n".join(out)


def main():
    """Offline/local audit only: does not import trading runtime or use secrets."""
    import argparse
    import asyncio
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--acceptance", default="live_acceptance.json")
    parser.add_argument("--venues", nargs="*", default=[])
    parser.add_argument("--html", action="store_true")
    args = parser.parse_args()
    try:
        report = asyncio.run(build(args.db, args.acceptance, args.venues))
    except (FileNotFoundError, ValueError, aiosqlite.Error) as error:
        parser.exit(2, f"Readiness audit unavailable: {error}\n")
    print(
        render(report)
        if args.html
        else json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    )


if __name__ == "__main__":
    main()
