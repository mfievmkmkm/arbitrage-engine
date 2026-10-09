"""Complete database audit, built on request without exposing credentials."""

import json
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
    "Funding Paper decisions": "funding_paper_decisions",
    "Funding Paper state": "funding_paper_state",
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
