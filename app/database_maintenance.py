"""Local SQLite online backup and non-overwriting inspection recovery copies.

No credentials, exchange clients, signing, retries or external uploads. Restored
copies require separate operator reconciliation, never restart trading silently.
"""

import hashlib
import json
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def existing(path):
    path = Path(path).absolute()
    if path.is_symlink() or not path.is_file():
        raise ValueError("MAINTENANCE_DATABASE_MISSING_OR_SYMLINK")
    return path


def connect(path, mode="ro"):
    return sqlite3.connect(
        existing(path).as_uri() + "?mode=" + mode,
        uri=True,
        timeout=2,
    )


def checksum(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def inspect(path):
    with closing(connect(path)) as db:
        db.execute("BEGIN")
        if [r[0] for r in db.execute("PRAGMA integrity_check")] != ["ok"]:
            raise ValueError("MAINTENANCE_INTEGRITY_FAILED")
        tables = [
            r[0]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        rows, watermarks = {}, {}
        for table in tables:
            name = '"' + table.replace('"', '""') + '"'
            rows[table] = db.execute("SELECT COUNT(*) FROM " + name).fetchone()[0]
            try:
                watermarks[table] = db.execute(
                    "SELECT MAX(rowid) FROM " + name
                ).fetchone()[0]
            except sqlite3.OperationalError:
                watermarks[table] = None
        return dict(
            integrity="ok",
            table_rows=rows,
            rowid_watermarks=watermarks,
            private_history_present=any(
                rows.get(t, 0)
                for t in (
                    "live_trades",
                    "order_intents",
                    "wallet_tx_intents",
                    "live_results",
                )
            ),
        )


def atomic_json(path, value):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())


def backup(source, directory, *, timeout=60):
    source = existing(source)
    if type(timeout) not in (int, float) or not 0 < timeout <= 300:
        raise ValueError("MAINTENANCE_TIMEOUT_INVALID")
    directory = Path(directory).absolute()
    if directory.is_symlink():
        raise ValueError("MAINTENANCE_BACKUP_DIRECTORY_SYMLINK")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(
        prefix=".sqlite-backup-", suffix=".tmp", dir=directory
    )
    os.close(fd)
    temporary = Path(temporary)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = directory / (
        "arbitrage-" + stamp + "-" + temporary.stem.rsplit("-", 1)[-1] + ".sqlite3"
    )
    deadline = time.monotonic() + timeout

    def progress(*_):
        if time.monotonic() > deadline:
            raise TimeoutError("MAINTENANCE_BACKUP_TIMEOUT")

    try:
        with closing(connect(source)) as original, closing(
            sqlite3.connect(temporary)
        ) as saved:
            original.backup(saved, pages=256, progress=progress, sleep=0.05)
            saved.execute("PRAGMA journal_mode=DELETE")
        details = inspect(temporary)
        with open(temporary, "rb") as stream:
            os.fsync(stream.fileno())
        os.link(temporary, target)
        manifest = dict(
            version=1,
            mode="SQLITE_ONLINE_BACKUP",
            created_at=time.time(),
            database_file=target.name,
            sha256=checksum(target),
            bytes=target.stat().st_size,
            execution_authority=False,
            contains_private_data=True,
            **details,
        )
        atomic_json(target.with_suffix(".manifest.json"), manifest)
        return dict(
            path=str(target),
            manifest=str(target.with_suffix(".manifest.json")),
            **manifest,
        )
    finally:
        temporary.unlink(missing_ok=True)


def verify(path):
    path = existing(path)
    if any(
        Path(str(path) + suffix).exists() and Path(str(path) + suffix).stat().st_size
        for suffix in ("-wal", "-journal")
    ):
        raise ValueError("MAINTENANCE_BACKUP_SIDECAR_NOT_SEALED")
    manifest_path = path.with_suffix(".manifest.json")
    if manifest_path.is_symlink():
        raise ValueError("MAINTENANCE_MANIFEST_SYMLINK")
    manifest = json.loads(manifest_path.read_text())
    if not isinstance(manifest, dict):
        raise ValueError("MAINTENANCE_MANIFEST_INVALID")
    if (
        type(manifest.get("version")) is not int
        or manifest["version"] != 1
        or manifest.get("database_file") != path.name
        or manifest.get("sha256") != checksum(path)
        or manifest.get("bytes") != path.stat().st_size
    ):
        raise ValueError("MAINTENANCE_BACKUP_DIGEST_CONFLICT")
    details = inspect(path)
    if any(manifest.get(k) != v for k, v in details.items()):
        raise ValueError("MAINTENANCE_BACKUP_MANIFEST_CONFLICT")
    return dict(path=str(path), **manifest, verified=True)


def restore_copy(source, destination):
    manifest = verify(source)
    destination = Path(destination).absolute()
    if (
        destination.exists()
        or destination.is_symlink()
        or any(
            Path(str(destination) + suffix).exists()
            for suffix in ("-wal", "-shm", "-journal")
        )
    ):
        raise FileExistsError("MAINTENANCE_RESTORE_NEVER_OVERWRITES")
    if not destination.parent.is_dir() or destination.parent.is_symlink():
        raise ValueError("MAINTENANCE_RESTORE_PARENT_INVALID")
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with closing(connect(source)) as original, closing(
            sqlite3.connect(destination)
        ) as restored:
            original.backup(restored)
            restored.execute(
                "CREATE TABLE IF NOT EXISTS database_recovery_hold(id INTEGER PRIMARY KEY CHECK(id=1),source_sha256 TEXT NOT NULL,created_at REAL NOT NULL,reason TEXT NOT NULL)"
            )
            restored.execute(
                "INSERT OR REPLACE INTO database_recovery_hold VALUES(1,?,?,?)",
                (
                    manifest["sha256"],
                    time.time(),
                    "RESTORED_COPY_REQUIRES_SEPARATE_RECONCILIATION",
                ),
            )
            restored.commit()
        inspect(destination)
        return dict(
            path=str(destination),
            mode="INSPECTION_ONLY_RECOVERY_COPY",
            startup_blocked=True,
            execution_authority=False,
            source_sha256=manifest["sha256"],
        )
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def ensure_not_restored(path):
    """Run before creating clients; even a malformed recovery marker fails closed."""
    path = Path(path)
    if not path.exists():
        return
    with closing(connect(path)) as db:
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='database_recovery_hold'"
        ).fetchone():
            raise RuntimeError(
                "DATABASE_RESTORED_COPY_REQUIRES_OPERATOR_RECONCILIATION"
            )


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    b = commands.add_parser("backup")
    b.add_argument("--db", required=True)
    b.add_argument("--directory", required=True)
    v = commands.add_parser("verify")
    v.add_argument("--backup", required=True)
    r = commands.add_parser("restore-copy")
    r.add_argument("--backup", required=True)
    r.add_argument("--destination", required=True)
    args = parser.parse_args()
    try:
        result = (
            backup(args.db, args.directory)
            if args.command == "backup"
            else (
                verify(args.backup)
                if args.command == "verify"
                else restore_copy(args.backup, args.destination)
            )
        )
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(2, f"Database maintenance refused: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
