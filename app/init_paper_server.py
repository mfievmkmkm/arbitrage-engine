"""Explicit fresh virtual experiment. Never overwrites a database or old state."""

import argparse
import json
import os
import sqlite3
from pathlib import Path

from dotenv import dotenv_values

from .dex_virtual_paper_source import routes_checked
from .paper_server import validate


def initialize(env_path, db_path):
    env, db = Path(env_path), Path(db_path).absolute()
    if not env.is_file() or env.is_symlink():
        raise ValueError("FRESH_PAPER_ENV_REQUIRED")
    values = dotenv_values(env)
    validate(values)
    try:
        if int(values.get("ADMIN_ID") or 0) <= 0:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("FRESH_PAPER_ADMIN_ID_REQUIRED") from None
    routes = routes_checked(json.loads(values.get("DEX_VIRTUAL_ROUTES_JSON") or "[]"))
    if any(r["chain_id"] != "solana" for r in routes) and not values.get("ZEROX_API_KEY"):
        raise ValueError("FRESH_PAPER_ZEROX_API_KEY_REQUIRED")
    if any(r["chain_id"] == "solana" for r in routes) and not values.get("JUPITER_API_KEY"):
        raise ValueError("FRESH_PAPER_JUPITER_API_KEY_REQUIRED")
    if not db.parent.is_dir() or db.parent.is_symlink():
        raise ValueError("FRESH_PAPER_DATA_DIRECTORY_REQUIRED")
    if any(db.parent.iterdir()):
        raise ValueError("FRESH_PAPER_DATA_DIRECTORY_NOT_EMPTY")
    # O_EXCL is the final no-overwrite authority, including concurrent callers.
    fd = os.open(db, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with sqlite3.connect(db) as connection:
            connection.execute("CREATE TABLE paper_experiment_origin(id INTEGER PRIMARY KEY CHECK(id=1), mode TEXT NOT NULL)")
            connection.execute("INSERT INTO paper_experiment_origin VALUES(1,'EXPLICIT_FRESH_VIRTUAL_PAPER')")
    except BaseException:
        # Only this invocation's exclusively created file is removed on failure.
        db.unlink(missing_ok=True)
        raise
    return db


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default=os.getenv("PAPER_SERVER_ENV", "/run/config/bot.env"))
    parser.add_argument("--db", default=os.getenv("DB_PATH", "/data/arbitrage.sqlite3"))
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--bot-stopped", action="store_true")
    args = parser.parse_args()
    if not args.fresh or not args.bot_stopped:
        parser.error("Confirm a NEW experiment and stop the old bot: --fresh --bot-stopped")
    try:
        initialize(args.env, args.db)
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(2, str(exc) + "\n")
    print("New virtual experiment initialized. Existing history was not imported.")
    print("Database is ready; start the bot separately. No real orders were submitted.")


if __name__ == "__main__":
    main()
