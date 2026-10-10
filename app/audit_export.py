"""Complete database audit, built on request without exposing credentials."""

import json
import time
import zipfile
from pathlib import Path
import aiosqlite
from .xlsx_export import write
from .export_service import write_csv
from .secret_redaction import redact

TABLES = {
    "Market books": "market_books",
    "Execution replay runs": "execution_replay_runs",
    "Execution replay results": "execution_replay_results",
    "Cash execution runs": "cash_execution_runs",
    "Cash execution results": "cash_execution_results",
    "Observations": "observations",
    "Strategy observations": "strategy_observations",
    "Paper trades": "paper_positions",
    "Paper marks": "paper_marks",
    "Spot futures trades": "spot_future_paper",
    "Spot futures marks": "spot_future_marks",
    "Spot spot trades": "spot_spot_paper",
    "Spot spot marks": "spot_spot_marks",
    "Spot spot inventory": "spot_spot_state",
    "Spot spot decisions": "spot_spot_decisions",
    "Funding Paper trades": "funding_paper",
    "Funding Paper marks": "funding_paper_marks",
    "Funding Paper events": "funding_paper_events",
    "Funding rate windows": "funding_rate_windows",
    "Funding execution runs": "funding_execution_runs",
    "Funding execution results": "funding_execution_results",
    "Funding Paper decisions": "funding_paper_decisions",
    "Funding Paper state": "funding_paper_state",
    "CEX DEX Paper trades": "cex_dex_paper",
    "CEX DEX Paper marks": "cex_dex_paper_marks",
    "CEX DEX Paper events": "cex_dex_paper_events",
    "CEX DEX Paper decisions": "cex_dex_paper_decisions",
    "CEX DEX Paper state": "cex_dex_paper_state",
    "DEX quote history": "dex_quote_history",
    "DEX stress runs": "dex_stress_runs",
    "DEX stress results": "dex_stress_results",
    "Wallet tx intents": "wallet_tx_intents",
    "Wallet tx events": "wallet_tx_events",
    "DEX Live stages": "dex_live_stages",
    "DEX Live events": "dex_live_events",
    "Execution events": "execution_events",
    "Order intents": "order_intents",
    "Order request evidence": "order_request_evidence",
    "Private order events": "private_order_events",
    "Live trades": "live_trades",
    "Live marks": "live_marks",
    "Live results": "live_results",
    "Live cash inventory": "live_cash_inventory",
    "Live spot allocations": "live_spot_allocations",
    "Live incidents": "live_incidents",
    "Monitor state": "live_monitor_state",
    "Funding settlements": "funding_settlements",
    "Ledger": "ledger",
    "Decisions": "signal_decisions",
}


async def build(path, directory):
    sheets = {}
    async with aiosqlite.connect(path) as d:
        d.row_factory = aiosqlite.Row
        await d.execute("BEGIN")
        async with d.execute("SELECT name FROM sqlite_master WHERE type='table'") as c:
            available = {x[0] for x in await c.fetchall()}
        for title, table in TABLES.items():
            if table not in available:
                continue
            async with d.execute(
                "SELECT * FROM " + table + " ORDER BY rowid LIMIT 50000"
            ) as c:
                rows = [dict(x) for x in await c.fetchall()]
            for row in rows:
                if "payload" in row:
                    try:
                        row["payload"] = json.loads(row["payload"])
                    except (ValueError, TypeError):
                        pass
            sheets[title] = redact(rows)
        from .live_execution_costs import read as execution_costs

        costs = await execution_costs(d, limit=1000)
        if costs["trades"]:
            sheets["Live cost attribution"] = redact(costs["trades"])
            sheets["Live order attribution"] = redact(costs["orders"])
        from .project_readiness import read as readiness_evidence

        evidence = await readiness_evidence(d, time.time())
        sheets["Paper OOS evidence"] = [
            dict(strategy=s, **r) for s, r in evidence["samples"].items()
        ]
        sheets["Stress evidence"] = [
            dict(strategy=s, **r) for s, r in evidence["execution_stress"].items()
        ]
        walks = evidence["walk_forward"]["strategies"]
        sheets["Walk forward summary"] = [
            dict(
                strategy=s,
                **{k: v for k, v in r.items() if k not in ("folds", "strategy")}
            )
            for s, r in walks.items()
        ]
        sheets["Walk forward folds"] = [
            dict(strategy=s, **fold) for s, r in walks.items() for fold in r["folds"]
        ]
        await d.rollback()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    workbook = write(directory / "arbitrage_audit.xlsx", sheets)
    archive = directory / "arbitrage_csv.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        report = {
            "mode": "AUDIT_READ_ONLY",
            "execution_authority": False,
            "table_rows": {name: len(rows) for name, rows in sheets.items()},
            "limitations": [
                "REST observations are not atomic fills",
                "Future funding is not realized PnL",
                "Data capped at 50000 rows per table",
            ],
            "instruction": "Suggest hypotheses and replay checks; never authorize trades or change risk limits.",
        }
        z.writestr(
            "ai_report_input.json", json.dumps(report, ensure_ascii=False, indent=2)
        )
        for title, rows in sheets.items():
            name = title.lower().replace(" ", "_") + ".csv"
            file = write_csv(directory / name, rows)
            z.write(file, name)
    return workbook, str(archive)
