"""Read-only delivery audit. Never changes LIVE gates or treats test count as release."""

import time
from html import escape
import aiosqlite
from .live_acceptance import accepted
from .spot_future_history_replay import build as replay

TABLES = {
    "futures_futures": "paper_positions",
    "spot_futures": "spot_future_paper",
    "spot_spot": "spot_spot_paper",
    "funding_arb": "funding_paper",
    "cex_dex": "cex_dex_paper",
}


async def build(path, acceptance_path, venues=(), now=None):
    now = time.time() if now is None else now
    counts, active, wallet_pending = {}, 0, 0
    async with aiosqlite.connect(path) as d:
        await d.execute("BEGIN")
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
    sample = {}
    for strategy, count in counts.items():
        report = await replay(path, strategy=strategy) if count else None
        sample[strategy] = dict(
            closed=count,
            eligible=report["eligible"] if report else 0,
            oos_model_positive=bool(report and report.get("model_positive")),
            reason=(
                report.get("status", "MODEL_REVIEW_ONLY")
                if report
                else "HISTORY_MISSING"
            ),
        )
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
        "DEX_WRITE_BOOTSTRAP_AND_PAIRED_EXIT_MONITOR_NOT_CONNECTED",
        "REAL_ACCOUNT_AND_WALLET_CERTIFICATION",
        "LONG_RUNNING_PAPER_OOS_AND_MICRO_CANARY",
    ]
    if active or wallet_pending:
        missing.append("UNRESOLVED_LIVE_OR_WALLET_EXPOSURE")
    return dict(
        mode="READ_ONLY_PROJECT_AUDIT",
        software_complete=False,
        production_ready=False,
        missing=missing,
        samples=sample,
        account_acceptance=account,
        active_live=active,
        wallet_pending=wallet_pending,
        implemented=[
            "CEX_FF_SF_SS_FUNDING_LIFECYCLES",
            "TWO_SIDED_DEX_PAPER_REPLAY",
            "DEX_SEQUENTIAL_EXECUTION_STRESS",
            "ISOLATED_WALLET_SIGNING_AND_NONCE_JOURNAL",
            "DUAL_RPC_FINALIZED_RECEIPT_CASHFLOW",
            "DEX_DURABLE_BRIDGE_SESSION_AND_CEX_BACKEND",
            "DEX_READ_ONLY_BRIDGE_RUNTIME_OBSERVER",
            "TELEGRAM_DIARY_EXPORT",
        ],
        live_allowed=False,
    )


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
        "\n<b>До окончательного завершения</b>\n• Подключить write-bootstrap CEX/DEX к scanner/shared monitor и динамическим NET-выходам. Durable session API уже реализован; runtime observer только читает.\n• Проверить выбранные аккаунты, изолированный кошелёк и scope разрешений.\n• Собрать длительную Paper/OOS-историю, затем отдельно провести micro-canary.",
        f"\nНезавершённых LIVE: {report['active_live']} · wallet intents: {report['wallet_pending']}",
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
    return "\n".join(out)
