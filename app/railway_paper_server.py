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
    data = volume / "paper"
    data.mkdir(exist_ok=True, mode=0o700)
    if data.is_symlink():
        raise ValueError("RAILWAY_PAPER_DATA_SYMLINK")
    values.update(DB_PATH=str(data / "arbitrage.sqlite3"),
                  RUNTIME_STATE_PATH=str(data / "runtime_state.json"),
                  LIVE_METRICS_PATH=str(data / "live_metrics.json"))
    stored = data / "watchlist.json"
    if values.get("DEX_VIRTUAL_ROUTES_JSON"):
        routes = json.loads(values["DEX_VIRTUAL_ROUTES_JSON"])
    elif stored.is_file():
        routes = json.loads(stored.read_text(encoding="utf-8"))["routes"]
    else:
        routes = default_routes("reference", 20)
    values["DEX_VIRTUAL_ROUTES_JSON"] = json.dumps(routes_checked(routes), separators=(",", ":"))
    db = data / "arbitrage.sqlite3"
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
    if preset == "none" or stored.exists():
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
        routes, report = await generate(json.loads(values["DEX_VIRTUAL_ROUTES_JSON"]), clients, provider, seeds)
        # Don't cache a fully failed Solana scan forever. Restart can retry after
        # API issues are fixed; existing positions/routes remain untouched.
        if not any(row["status"] == "CONFIGURED" for row in report):
            for row in report:
                print("Watchlist:", row["base"], row.get("reason", "SKIPPED"), flush=True)
            raise ValueError("RAILWAY_PAPER_NO_SOLANA_ROUTES: use preset none for Ethereum only")
        temporary = data / (".watchlist-" + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(dict(routes=routes, report=report), stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
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
    data = prepare_environment(os.environ, "/data")
    asyncio.run(expand_watchlist(os.environ, data))
    run_database(Path(os.environ["DB_PATH"]))


if __name__ == "__main__":
    main()
