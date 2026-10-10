import asyncio
import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app.database_maintenance import backup, verify, restore_copy, ensure_not_restored
from app.retention import prune


def seed(path):
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE observations(id INTEGER PRIMARY KEY, ts REAL, payload TEXT)"
        )
        db.executemany(
            "INSERT INTO observations VALUES(?,?,?)",
            [(1, 100, "old"), (2, 1000000, "new")],
        )
        for table in (
            "execution_events",
            "order_intents",
            "wallet_tx_intents",
            "paper_marks",
            "live_results",
            "funding_settlements",
        ):
            db.execute(
                f"CREATE TABLE {table}(id INTEGER PRIMARY KEY,ts REAL,payload TEXT)"
            )
            db.execute(f"INSERT INTO {table} VALUES(1,0,'proof')")


def test_online_backup_includes_uncheckpointed_wal_and_is_private(tmp_path):
    path = tmp_path / "wal.db"
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA wal_autocheckpoint=0")
    db.execute("CREATE TABLE journal(id INTEGER PRIMARY KEY,payload TEXT)")
    db.execute("INSERT INTO journal VALUES(1,'latest WAL proof')")
    db.commit()
    assert Path(str(path) + "-wal").exists()
    saved = backup(path, tmp_path / "backups")
    result = verify(saved["path"])
    assert result["verified"] and result["table_rows"]["journal"] == 1
    with sqlite3.connect(saved["path"]) as check:
        assert (
            check.execute("SELECT payload FROM journal").fetchone()[0]
            == "latest WAL proof"
        )
    assert os.stat(saved["path"]).st_mode & 0o777 == 0o600
    assert os.stat(saved["manifest"]).st_mode & 0o777 == 0o600
    db.close()


def test_unique_backups_never_overwrite_and_source_unchanged(tmp_path):
    path = tmp_path / "source.db"
    seed(path)
    original = path.read_bytes()
    a = backup(path, tmp_path / "copies")
    b = backup(path, tmp_path / "copies")
    assert a["path"] != b["path"]
    assert path.read_bytes() == original
    assert verify(a["path"])["private_history_present"]


def test_tampered_database_or_manifest_refused(tmp_path):
    path = tmp_path / "source.db"
    seed(path)
    saved = backup(path, tmp_path / "copies")
    with sqlite3.connect(saved["path"]) as db:
        db.execute("UPDATE observations SET payload='changed'")
    with pytest.raises(ValueError, match="DIGEST_CONFLICT"):
        verify(saved["path"])
    saved = backup(path, tmp_path / "copies")
    manifest = Path(saved["manifest"])
    data = json.loads(manifest.read_text())
    data["table_rows"]["observations"] = 0
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="MANIFEST_CONFLICT"):
        verify(saved["path"])


def test_missing_or_corrupt_source_no_fake_backup(tmp_path):
    with pytest.raises(ValueError):
        backup(tmp_path / "missing.db", tmp_path / "copies")
    path = tmp_path / "bad.db"
    path.write_bytes(b"not SQLite")
    with pytest.raises(sqlite3.DatabaseError):
        backup(path, tmp_path / "copies")
    assert not list((tmp_path / "copies").glob("*.sqlite3"))


def test_symlink_source_or_output_directory_rejected(tmp_path):
    path = tmp_path / "source.db"
    seed(path)
    alias = tmp_path / "alias.db"
    alias.symlink_to(path)
    with pytest.raises(ValueError, match="SYMLINK"):
        backup(alias, tmp_path / "copies")
    directory = tmp_path / "real"
    directory.mkdir()
    link = tmp_path / "link"
    link.symlink_to(directory)
    with pytest.raises(ValueError, match="SYMLINK"):
        backup(path, link)


def test_restore_never_overwrites_current_database_or_sidecars(tmp_path):
    path = tmp_path / "source.db"
    seed(path)
    saved = backup(path, tmp_path / "copies")
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        restore_copy(saved["path"], path)
    assert path.read_bytes() == original
    target = tmp_path / "restored.db"
    Path(str(target) + "-wal").write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        restore_copy(saved["path"], target)
    assert not target.exists()


def test_restore_is_inspection_copy_and_startup_guard_precedes_private_clients(
    tmp_path,
):
    path = tmp_path / "source.db"
    seed(path)
    saved = backup(path, tmp_path / "copies")
    restored = restore_copy(saved["path"], tmp_path / "restored.db")
    assert restored["startup_blocked"] and not restored["execution_authority"]
    with sqlite3.connect(restored["path"]) as db:
        assert db.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 1
    with pytest.raises(RuntimeError, match="RECONCILIATION"):
        ensure_not_restored(restored["path"])
    ensure_not_restored(path)
    import inspect
    import app.main as runtime

    code = inspect.getsource(runtime.main)
    assert code.index("ensure_not_restored(config.db_path)") < code.index(
        "build_private_readers()"
    )


def test_retention_defaults_to_dry_run_and_preserves_all_history(tmp_path):
    path = tmp_path / "source.db"
    seed(path)
    before = path.read_bytes()
    report = asyncio.run(prune(NS(path=path), observation_days=1, now=1000000))
    assert (
        report["dry_run"] and report["observations"] == report["execution_events"] == 0
    )
    assert report["eligible_observations"] == 1
    assert path.read_bytes() == before


def test_apply_requires_verified_backup_and_never_deletes_financial_proofs(tmp_path):
    path = tmp_path / "source.db"
    seed(path)
    with pytest.raises(ValueError, match="BACKUP_REQUIRED"):
        asyncio.run(prune(NS(path=path), dry_run=False))
    report = asyncio.run(
        prune(
            NS(path=path),
            1,
            dry_run=False,
            backup_directory=tmp_path / "copies",
            now=1000000,
        )
    )
    assert report["observations"] == 1 and report["execution_events"] == 0
    assert verify(report["backup_path"])["table_rows"]["observations"] == 2
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
        for t in (
            "execution_events",
            "order_intents",
            "wallet_tx_intents",
            "paper_marks",
            "live_results",
            "funding_settlements",
        ):
            assert db.execute(f"SELECT payload FROM {t}").fetchone()[0] == "proof"


def test_concurrent_new_or_changed_old_rows_are_not_deleted_without_backup(
    tmp_path, monkeypatch
):
    import app.retention as retention

    path = tmp_path / "source.db"
    seed(path)
    real_verify = retention.verify

    def mutate_after_backup(saved):
        result = real_verify(saved)
        with sqlite3.connect(path) as db:
            db.execute("INSERT INTO observations VALUES(3,1,'late insertion')")
            db.execute("UPDATE observations SET payload='concurrent edit' WHERE id=1")
        return result

    monkeypatch.setattr(retention, "verify", mutate_after_backup)
    report = asyncio.run(
        prune(
            NS(path=path),
            1,
            dry_run=False,
            backup_directory=tmp_path / "copies",
            now=1000000,
        )
    )
    assert report["observations"] == 0
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 3


@pytest.mark.parametrize("days", [0, -1, True, float("nan"), float("inf")])
def test_bad_retention_policy_never_mutates(tmp_path, days):
    path = tmp_path / "source.db"
    seed(path)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        asyncio.run(prune(NS(path=path), days))
    assert path.read_bytes() == before


def test_failed_backup_prevents_prune(tmp_path, monkeypatch):
    import app.retention as retention

    path = tmp_path / "source.db"
    seed(path)

    def failure(*args, **kwargs):
        raise ValueError("backup failure")

    monkeypatch.setattr(retention, "backup", failure)
    with pytest.raises(ValueError):
        asyncio.run(
            prune(NS(path=path), dry_run=False, backup_directory=tmp_path / "copies")
        )
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 2


def test_cli_backup_verify_and_dry_run(tmp_path):
    import subprocess
    import sys

    path = tmp_path / "source.db"
    seed(path)
    first = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.database_maintenance",
            "backup",
            "--db",
            str(path),
            "--directory",
            str(tmp_path / "copies"),
        ],
        capture_output=True,
        text=True,
    )
    assert first.returncode == 0, first.stderr
    saved = json.loads(first.stdout)
    checked = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.database_maintenance",
            "verify",
            "--backup",
            saved["path"],
        ],
        capture_output=True,
        text=True,
    )
    assert checked.returncode == 0 and json.loads(checked.stdout)["verified"]
    dry = subprocess.run(
        [sys.executable, "-m", "app.retention", "--db", str(path)],
        capture_output=True,
        text=True,
    )
    assert dry.returncode == 0 and json.loads(dry.stdout)["dry_run"]


def test_backup_is_sealed_without_wal_and_unsealed_mutations_refused(tmp_path):
    path = tmp_path / "source.db"
    seed(path)
    saved = backup(path, tmp_path / "copies")
    with sqlite3.connect(saved["path"]) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    Path(saved["path"] + "-wal").write_bytes(b"unsealed mutation")
    with pytest.raises(ValueError, match="SIDECAR_NOT_SEALED"):
        verify(saved["path"])


def test_restored_copy_visible_in_readiness(tmp_path):
    from app.project_readiness import build

    path = tmp_path / "source.db"
    sqlite3.connect(path).close()
    saved = backup(path, tmp_path / "copies")
    restored = restore_copy(saved["path"], tmp_path / "restored.db")
    report = asyncio.run(build(restored["path"], tmp_path / "missing.json", now=100))
    assert report["restored_copy"]
    assert "RESTORED_COPY_REQUIRES_OPERATOR_RECONCILIATION" in report["missing"]


def test_concurrent_schema_change_prevents_prune(tmp_path, monkeypatch):
    import app.retention as retention

    path = tmp_path / "source.db"
    seed(path)
    original = retention.verify

    def migrated(saved):
        result = original(saved)
        with sqlite3.connect(path) as db:
            db.execute(
                "ALTER TABLE observations RENAME COLUMN payload TO newer_payload"
            )
        return result

    monkeypatch.setattr(retention, "verify", migrated)
    with pytest.raises(ValueError, match="SCHEMA_CHANGED"):
        asyncio.run(
            prune(
                NS(path=path),
                1,
                dry_run=False,
                backup_directory=tmp_path / "copies",
                now=1000000,
            )
        )
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 2
