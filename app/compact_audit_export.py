"""Bounded, streaming read-only audit; no Excel or readiness recomputation."""
import asyncio
import csv
import io
import json
import sqlite3
import time
import zipfile
from pathlib import Path
from .audit_export import TABLES
from .secret_redaction import redact


def selected_tables():
    return {title: table for title, table in TABLES.items()
            if title == "Channel reader state"
            or "trades" in title.lower()
            or title in ("Paper trades", "Paper marks", "Decisions", "Ledger", "Scan cycles")
            or (title.startswith(("DEX channel", "DEX Bybit", "DEX paired", "CEX DEX Paper", "DEX virtual"))
                and title.endswith(("marks", "decisions", "state", "events")))}


def build_sync(path, directory, row_limit=5000):
    if type(row_limit) is not int or not 1 <= row_limit <= 10000:
        raise ValueError("EXPORT_ROW_LIMIT_INVALID")
    output = Path(directory) / "arbitrage_compact_audit.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    report = dict(mode="AUDIT_READ_ONLY", generated_at=time.time(),
                  execution_authority=False, tables={},
                  limitations=["Newest rows only when a table exceeds the stated limit",
                               "Model prices are not real executions",
                               "Full database and old observations remain unchanged"])
    # A separate connection in the worker thread, never a shared trading connection.
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=5) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        available = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
            for title, table in selected_tables().items():
                if table not in available:
                    continue
                total = db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                cursor = db.execute("SELECT * FROM " + table + " ORDER BY rowid DESC LIMIT ?", (row_limit,))
                columns = [c[0] for c in cursor.description]
                info = dict(total_rows=total, exported_rows=0, limit=row_limit,
                            truncated=total > row_limit, order="newest_first")
                filename = title.lower().replace(" ", "_") + ".csv"
                with archive.open(filename, "w", force_zip64=True) as raw:
                    with io.TextIOWrapper(raw, encoding="utf-8", newline="") as stream:
                        writer = csv.DictWriter(stream, fieldnames=columns)
                        writer.writeheader()
                        for row in cursor:
                            data = dict(row)
                            if "payload" in data:
                                try:
                                    data["payload"] = json.loads(data["payload"])
                                except (ValueError, TypeError):
                                    # Unknown payload cannot be safely inspected for secrets.
                                    data["payload"] = "[UNPARSEABLE_PAYLOAD_OMITTED]"
                            data = redact(data)
                            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list, tuple)) else v
                                             for k, v in data.items()})
                            info["exported_rows"] += 1
                report["tables"][title] = info
            archive.writestr("manifest.json", json.dumps(report, ensure_ascii=False, indent=2))
        db.rollback()
    if output.stat().st_size > 45 * 1024 * 1024:
        raise ValueError("EXPORT_TOO_LARGE: reduce row limit")
    return str(output)


async def build(path, directory, row_limit=5000):
    return await asyncio.to_thread(build_sync, path, directory, row_limit)
