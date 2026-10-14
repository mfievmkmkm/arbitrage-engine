"""Linux container entrypoint: persisted history, single local instance, virtual only."""

import os
import runpy
import sqlite3
from pathlib import Path

from dotenv import dotenv_values, load_dotenv


def validate(values):
    truthy = lambda v: str(v or "").strip().lower() in ("true", "1", "yes", "on")
    if any(truthy(values.get(k)) for k in ("LIVE_ENABLED", "LIVE_ENTRY_ENABLED", "DEX_LIVE_ENABLED")):
        raise ValueError("PAPER_SERVER_LIVE_FLAGS_MUST_BE_FALSE")
    if not truthy(values.get("DEX_VIRTUAL_PAPER_ENABLED")):
        raise ValueError("PAPER_SERVER_VIRTUAL_MODE_REQUIRED")
    if not values.get("BOT_TOKEN"):
        raise ValueError("PAPER_SERVER_BOT_TOKEN_MISSING")


def main():
    config_path = os.getenv("PAPER_SERVER_ENV", "/run/config/bot.env")
    if not Path(config_path).is_file():
        raise ValueError("PAPER_SERVER_ENV_FILE_MISSING")
    values = dotenv_values(config_path)
    validate(values)
    load_dotenv(config_path, override=False)
    validate(os.environ)
    # The volume must already contain the migrated database: don't silently
    # start a fresh ledger when a mount/path was misspelled.
    db = Path(os.getenv("DB_PATH", "/data/arbitrage.sqlite3"))
    run_database(db)


def run_database(db):
    db = Path(db).absolute()
    if not db.is_file() or db.is_symlink():
        raise ValueError("PAPER_SERVER_MIGRATED_DATABASE_REQUIRED")
    with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as connection:
        if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise ValueError("PAPER_SERVER_DATABASE_CHECK_FAILED")
    import fcntl
    with (db.parent / "paper.instance.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("PAPER_SERVER_INSTANCE_ALREADY_RUNNING") from None
        runpy.run_module("app.main", run_name="__main__")


if __name__ == "__main__":
    main()
