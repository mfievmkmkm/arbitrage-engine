"""Prepare a private migration directory locally; no uploads or credential output.

Stop the Windows bot first so SQLite and companion JSON states share a cutoff.
"""

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

from dotenv import dotenv_values

from .database_maintenance import backup, verify, checksum
from .dex_virtual_paper_source import routes_checked
from .paper_server import validate

STATE_SUFFIXES = ('', '.strategies.json', '.venues.json', '.stop.json',
                  '.paired_migration.json', '.paper_controls.json', '.trading_controls.json')

def resolve_local(root, name):
    p = Path(name)
    return p if p.is_absolute() else root / p


def verify_bundle(directory):
    root = Path(directory).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("version") != 1 or manifest.get("mode") != "VIRTUAL_PAPER_MIGRATION":
        raise ValueError("MIGRATION_MANIFEST_INVALID")
    allowed = {"bot.env", "data/arbitrage.sqlite3"} | {"data/runtime_state.json" + s for s in STATE_SUFFIXES}
    files = manifest.get("files", {})
    if not isinstance(files, dict) or not {"bot.env", "data/arbitrage.sqlite3"} <= files.keys() or not files.keys() <= allowed:
        raise ValueError("MIGRATION_MANIFEST_FILES_INVALID")
    for name, digest in files.items():
        path = root / name
        if path.is_symlink() or not path.is_file() or checksum(path) != digest:
            raise ValueError("MIGRATION_FILE_DIGEST_MISMATCH")
    return True


def prepare(env_path, destination):
    env = Path(env_path).resolve()
    if not env.is_file() or env.is_symlink():
        raise ValueError("MIGRATION_ENV_REQUIRED")
    values = dotenv_values(env)
    validate(values)
    routes_checked(json.loads(values.get("DEX_VIRTUAL_ROUTES_JSON") or "[]"))
    target = Path(destination).absolute()
    if target.exists():
        raise ValueError("MIGRATION_DESTINATION_EXISTS")
    target.parent.mkdir(parents=True, exist_ok=True)
    root = env.parent
    db = resolve_local(root, values.get("DB_PATH") or "arbitrage.sqlite3")
    with tempfile.TemporaryDirectory(prefix="paper-migration-", dir=target.parent) as temporary:
        stage = Path(temporary)
        saved = backup(db, stage / "backup")
        verified = verify(saved["path"])
        data = stage / "bundle" / "data"
        data.mkdir(parents=True, mode=0o700)
        shutil.copy2(saved["path"], data / "arbitrage.sqlite3")
        state = resolve_local(root, values.get("RUNTIME_STATE_PATH") or "runtime_state.json")
        files = {}
        for suffix in STATE_SUFFIXES:
            source = Path(str(state) + suffix)
            if source.is_symlink():
                raise ValueError("MIGRATION_STATE_SYMLINK")
            if source.exists():
                json.loads(source.read_text(encoding="utf-8"))
                name = "runtime_state.json" + suffix
                shutil.copy2(source, data / name)
                files["data/" + name] = checksum(data / name)
        # Raw credentials remain in a separate read-only mounted file; Docker
        # never bakes them into an image or interpolates their contents.
        shutil.copy2(env, stage / "bundle" / "bot.env")
        os.chmod(stage / "bundle" / "bot.env", 0o600)
        files["bot.env"] = checksum(stage / "bundle" / "bot.env")
        files["data/arbitrage.sqlite3"] = checksum(data / "arbitrage.sqlite3")
        manifest = dict(version=1, mode="VIRTUAL_PAPER_MIGRATION", contains_credentials=True,
                        live_allowed=False, files=files, database_sha256=saved["sha256"],
                        table_rows=verified["table_rows"], cutoff="OPERATOR_STOPPED_BOT_REQUIRED")
        (stage / "bundle" / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        os.rename(stage / "bundle", target)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default=".env")
    parser.add_argument("--output", default="server-transfer")
    parser.add_argument("--bot-stopped", action="store_true", help="Confirm local bot is stopped before snapshot")
    parser.add_argument("--verify", help="Verify a transferred directory before first server start")
    args = parser.parse_args()
    if args.verify:
        try:
            verify_bundle(args.verify)
        except (ValueError, OSError) as exc:
            parser.exit(2, str(exc) + "\n")
        print("Migration files verified. Start only one bot instance.")
        return
    if not args.bot_stopped:
        parser.error("Stop bot with Ctrl+C, then pass --bot-stopped")
    try:
        target = prepare(args.env, args.output)
    except (ValueError, OSError) as exc:
        parser.exit(2, str(exc) + "\n")
    print("Prepared:", target.name)
    print("Contains your API keys and bot token. Transfer over SSH; do not send to chats or Git.")
    print("Keep Windows bot stopped while server runs. No server has been started by this command.")


if __name__ == "__main__":
    main()
