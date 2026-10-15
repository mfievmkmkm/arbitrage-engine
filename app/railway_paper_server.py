"""Railway Variables + persistent Volume. No secrets written to config files."""

import asyncio
import json
import os
import uuid
from pathlib import Path

from .configure_virtual_paper import default_routes
from .dex_virtual_paper_source import routes_checked
from .init_paper_server import initialize_values
from .paper_server import validate, run_database


def persistent_path(value, volume, relative_to=None):
    path = Path(value)
    if not path.is_absolute() and path.parent == Path('.'):
        path = (relative_to or volume) / path
    if not path.is_absolute() or '..' in path.parts or not path.is_relative_to(volume):
        raise ValueError('RAILWAY_PAPER_PATH_OUTSIDE_VOLUME')
    current = path
    while current != volume:
        if current.is_symlink():
            raise ValueError('RAILWAY_PAPER_PATH_SYMLINK')
        current = current.parent
    return path


def database_path(values, volume):
    # Older containers used /data directly. Do not hide that history by
    # unconditionally replacing DB_PATH with /data/paper/arbitrage.sqlite3.
    explicit = values.get('DB_PATH')
    candidates = [directory / name for directory in (volume, volume / 'paper')
                  for name in ('arbitrage.sqlite3', 'arbitrage.db')
                  if (directory / name).exists() or (directory / name).is_symlink()]
    if explicit:
        if not Path(explicit).is_absolute():
            matches = [p for p in candidates if p.name == explicit]
            if len(matches) > 1:
                raise ValueError('RAILWAY_PAPER_DATABASE_AMBIGUOUS: set an absolute DB_PATH')
            selected = persistent_path(matches[0] if matches else explicit, volume,
                                       relative_to=volume / 'paper')
        else:
            selected = persistent_path(explicit, volume)
        if not selected.exists() and candidates:
            raise ValueError('RAILWAY_PAPER_CONFIGURED_DB_MISSING: existing database found; correct DB_PATH, do not reset')
        return selected
    if len(candidates) > 1:
        raise ValueError('RAILWAY_PAPER_DATABASE_AMBIGUOUS: set DB_PATH to the existing database')
    if candidates:
        return persistent_path(candidates[0], volume)
    return volume / 'paper' / 'arbitrage.sqlite3'


def prepare_environment(values, volume):
    validate(values)
    try:
        if int(values.get("ADMIN_ID") or 0) <= 0:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("RAILWAY_PAPER_ADMIN_ID_REQUIRED") from None
    if str(values.get("RAILWAY_VOLUME_MOUNT_PATH", "")) != str(volume):
        raise ValueError("RAILWAY_PAPER_VOLUME_REQUIRED: attach a Volume at /data")
    volume = Path(volume)
    if not volume.is_dir() or volume.is_symlink():
        raise ValueError("RAILWAY_PAPER_VOLUME_UNAVAILABLE")
    db = database_path(values, volume)
    if db == volume or (db.exists() and not db.is_file()):
        raise ValueError('RAILWAY_PAPER_DATABASE_NOT_FILE')
    data = db.parent
    data.mkdir(exist_ok=True, mode=0o700)
    if data.is_symlink():
        raise ValueError("RAILWAY_PAPER_DATA_SYMLINK")
    if not db.exists() and data != volume:
        # A missing ledger beside an older runtime must never turn into a new
        # experiment merely because the new default uses a subdirectory.
        if any(volume.glob('*.db')) or any(volume.glob('*.sqlite*')) or any(volume.glob('runtime_state.json*')):
            raise ValueError('RAILWAY_PAPER_OLD_DATA_NOT_EMPTY: locate the existing database before starting')
    values.update(DB_PATH=str(db),
                  RUNTIME_STATE_PATH=str(persistent_path(values['RUNTIME_STATE_PATH'], volume, data))
                  if values.get('RUNTIME_STATE_PATH') else str(data / 'runtime_state.json'),
                  LIVE_METRICS_PATH=str(persistent_path(values['LIVE_METRICS_PATH'], volume, data))
                  if values.get('LIVE_METRICS_PATH') else str(data / 'live_metrics.json'))
    stored = data / "watchlist.json"
    if values.get("DEX_VIRTUAL_ROUTES_JSON"):
        routes = json.loads(values["DEX_VIRTUAL_ROUTES_JSON"])
    elif stored.is_file():
        routes = json.loads(stored.read_text(encoding="utf-8"))["routes"]
    else:
        routes = default_routes("reference", 30)
    values["DEX_VIRTUAL_ROUTES_JSON"] = json.dumps(routes_checked(routes), separators=(",", ":"))
    if not db.exists():
        if values.get("PAPER_SERVER_FRESH_START", "").strip().lower() != "true":
            raise ValueError("RAILWAY_PAPER_FRESH_START_CONFIRMATION_REQUIRED")
        initialize_values(values, db)
        print("Railway: initialized a NEW virtual experiment; no history imported.", flush=True)
    elif db.is_symlink():
        raise ValueError("RAILWAY_PAPER_DATABASE_SYMLINK")
    return data


async def expand_watchlist(values, data):
    preset = values.get("DEX_VIRTUAL_WATCHLIST_PRESET", "none")
    if preset not in ("none", "core", "channel"):
        raise ValueError("RAILWAY_PAPER_WATCHLIST_PRESET_INVALID")
    stored = data / "watchlist.json"
    revision = values.get("DEX_VIRTUAL_WATCHLIST_REVISION", "1")
    if not revision or len(revision) > 64 or not all(c.isalnum() or c in "_-" for c in revision):
        raise ValueError("RAILWAY_PAPER_WATCHLIST_REVISION_INVALID")
    cached = json.loads(stored.read_text(encoding="utf-8")) if stored.exists() else None
    if preset == "none" or (cached and cached.get("revision", "1") == revision):
        return
    if not values.get("JUPITER_API_KEY"):
        raise ValueError("JUPITER_API_KEY_REQUIRED_FOR_WATCHLIST")
    from .build_virtual_watchlist import generate, SEEDS
    from .public_client_factory import build, close
    from .jupiter_research import Provider
    venues = [x.strip() for x in values.get("EXCHANGES", "bybit,mexc").split(",") if x.strip()]
    if not venues or len(venues) > 7:
        raise ValueError("RAILWAY_PAPER_EXCHANGES_INVALID")
    clients, provider = await build(venues, "swap"), Provider(values["JUPITER_API_KEY"])
    try:
        if not clients:
            raise ValueError("WATCHLIST_CEX_CONNECTIONS_UNAVAILABLE")
        seeds = SEEDS if preset == "channel" else {k: SEEDS[k] for k in ("SOL", "JUP")}
        existing = json.loads(values["DEX_VIRTUAL_ROUTES_JSON"])
        if cached:
            from .dex_virtual_paper_source import route_key
            preserved = list(routes_checked(cached["routes"]))
            keys = {route_key(r) for r in preserved}
            for r in existing:
                if route_key(r) not in keys:
                    preserved.append(r)
                    keys.add(route_key(r))
            existing = routes_checked(preserved)
        routes, report = await generate(existing, clients, provider, seeds)
        # An optional failed discovery must not stop already configured Paper
        # routes. Don't cache a failure as a successful refresh revision.
        if not any(row["status"] == "CONFIGURED" for row in report):
            for row in report:
                print("Watchlist:", row["base"], row.get("reason", "SKIPPED"), flush=True)
            print("Watchlist: no accepted new Solana quotes; continuing existing virtual routes.", flush=True)
            return
        temporary = data / (".watchlist-" + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(dict(routes=routes, report=report, revision=revision), stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            if cached:
                # Refresh extends the existing route list. Open model positions
                # keep their original identities and quantities.
                os.replace(temporary, stored)
            else:
                os.link(temporary, stored)
        finally:
            temporary.unlink(missing_ok=True)
        values["DEX_VIRTUAL_ROUTES_JSON"] = json.dumps(routes, separators=(",", ":"))
        for row in report:
            print("Watchlist:", row["base"], row["status"], row.get("venue", ""), row.get("reason", ""), flush=True)
    finally:
        await close(clients)
        await provider.close()


def main():
    # Defaults limit retained public books on a small trial Volume. They don't
    # bound the whole audit database; monitor actual Volume and RAM usage.
    defaults = dict(LIVE_ENABLED="false", LIVE_ENTRY_ENABLED="false", DEX_LIVE_ENABLED="false",
                    DEX_VIRTUAL_PAPER_ENABLED="true", EXCHANGES="bybit,mexc",
                    PAPER_CAPITAL_USD="50", BOOK_HISTORY_MAX_ROWS="20000", BOOK_HISTORY_HOURS="24")
    for k, v in defaults.items():
        os.environ.setdefault(k, v)
    from .paired_paper_profile import configure
    configure(os.environ)
    data = prepare_environment(os.environ, "/data")
    asyncio.run(expand_watchlist(os.environ, data))
    run_database(Path(os.environ["DB_PATH"]))


if __name__ == "__main__":
    main()
