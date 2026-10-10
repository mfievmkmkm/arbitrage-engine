"""Explicit backup-before-prune of scanner observations only.

Financial journals, requests, receipts, decisions, Paper marks and settlements
are never age-deleted. Newer concurrent rows beyond the backup watermark survive.
"""

import asyncio
import json
import math
import time
from contextlib import closing
from .database_maintenance import connect
from pathlib import Path

import aiosqlite
from .database_maintenance import backup, verify


async def prune(
    diary,
    observation_days=30,
    execution_days=180,
    *,
    dry_run=True,
    backup_directory=None,
    now=None,
):
    if type(dry_run) is not bool or any(
        type(x) not in (int, float) or not math.isfinite(x) or x <= 0
        for x in (observation_days, execution_days)
    ):
        raise ValueError("RETENTION_POLICY_INVALID")
    now = time.time() if now is None else now
    if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
        raise ValueError("RETENTION_TIME_INVALID")
    path = Path(diary.path).absolute()
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError("RETENTION_DATABASE_MISSING_OR_SYMLINK")
    cutoff = now - observation_days * 86400
    if not math.isfinite(cutoff):
        raise ValueError("RETENTION_TIME_INVALID")
    proof = None
    watermark = None
    if not dry_run:
        if backup_directory is None:
            raise ValueError("RETENTION_VERIFIED_BACKUP_REQUIRED")
        saved = await asyncio.to_thread(backup, path, backup_directory)
        proof = await asyncio.to_thread(verify, saved["path"])
        watermark = proof["rowid_watermarks"].get("observations")
    uri = path.as_uri() + "?mode=" + ("ro" if dry_run else "rw")
    async with aiosqlite.connect(uri, uri=True) as db:
        await db.execute("BEGIN" if dry_run else "BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='observations'"
        ) as c:
            present = await c.fetchone()
        if present:
            where, values = "ts<?", (cutoff,)
            if not dry_run:
                where += " AND rowid<=?"
                values += (watermark if watermark is not None else -1,)
            async with db.execute(
                "SELECT COUNT(*) FROM observations WHERE " + where, values
            ) as c:
                eligible = (await c.fetchone())[0]
            if not dry_run:
                # A rowid watermark alone does not protect concurrent edits to
                # an existing observation. Delete only exact backed-up rows.
                eligible = 0
                with closing(connect(proof["path"])) as saved:
                    candidates = saved.execute(
                        "SELECT rowid,* FROM observations WHERE ts<?", (cutoff,)
                    )
                    while batch := candidates.fetchmany(500):
                        ids = tuple(r[0] for r in batch)
                        async with db.execute(
                            "SELECT rowid,* FROM observations WHERE rowid IN ("
                            + ",".join("?" for _ in ids)
                            + ")",
                            ids,
                        ) as c:
                            if tuple(x[0] for x in c.description) != tuple(
                                x[0] for x in candidates.description
                            ):
                                raise ValueError(
                                    "RETENTION_SCHEMA_CHANGED_SINCE_BACKUP"
                                )
                            current = {r[0]: tuple(r) for r in await c.fetchall()}
                        identical = [
                            (r[0],) for r in batch if current.get(r[0]) == tuple(r)
                        ]
                        if identical:
                            await db.executemany(
                                "DELETE FROM observations WHERE rowid=?", identical
                            )
                            eligible += len(identical)
        else:
            eligible = 0
        if dry_run:
            await db.rollback()
        else:
            await db.commit()
    return dict(
        mode="RETENTION_DRY_RUN" if dry_run else "BACKUP_VERIFIED_OBSERVATION_PRUNE",
        dry_run=dry_run,
        eligible_observations=eligible,
        observations=0 if dry_run else eligible,
        execution_events=0,
        financial_history_preserved=True,
        execution_authority=False,
        backup_path=proof["path"] if proof else None,
        backup_sha256=proof["sha256"] if proof else None,
        backup_watermark=watermark,
        cutoff=cutoff,
    )


def main():
    import argparse
    from types import SimpleNamespace

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--observation-days", type=float, default=30)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-directory")
    args = parser.parse_args()
    try:
        report = asyncio.run(
            prune(
                SimpleNamespace(path=args.db),
                args.observation_days,
                dry_run=not args.apply,
                backup_directory=args.backup_directory,
            )
        )
    except (ValueError, OSError, aiosqlite.Error) as error:
        parser.exit(2, f"Retention refused: {error}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
