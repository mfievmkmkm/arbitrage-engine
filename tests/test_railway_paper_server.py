import asyncio
import json
import sqlite3

import pytest

from app.railway_paper_server import prepare_environment, expand_watchlist, DEFAULT_PAPER_EXCHANGES
from app.configure_virtual_paper import default_routes


def test_railway_paper_default_scans_all_supported_public_venues():
    from app.engine import ALLOWED
    assert set(DEFAULT_PAPER_EXCHANGES.split(',')) == ALLOWED


def environment(volume, **changes):
    return dict(BOT_TOKEN="placeholder", ADMIN_ID="123", ZEROX_API_KEY="placeholder",
                LIVE_ENABLED="false", DEX_VIRTUAL_PAPER_ENABLED="true",
                RAILWAY_VOLUME_MOUNT_PATH=str(volume), PAPER_SERVER_FRESH_START="true", **changes)


@pytest.mark.parametrize('name', ['arbitrage.sqlite3', 'arbitrage.db'])
@pytest.mark.parametrize('explicit', [False, True])
def test_existing_root_database_and_runtime_are_reused_without_fresh_start(tmp_path, name, explicit):
    db = tmp_path / name
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE history(net REAL)')
        c.execute('INSERT INTO history VALUES(-7.5)')
    (tmp_path / 'runtime_state.json.paper_controls.json').write_text('{"paused":true}')
    before = db.read_bytes()
    values = environment(tmp_path)
    values['PAPER_SERVER_FRESH_START'] = 'false'
    if explicit:
        values['DB_PATH'] = str(db)
    assert prepare_environment(values, tmp_path) == tmp_path
    assert values['DB_PATH'] == str(db)
    assert values['RUNTIME_STATE_PATH'] == str(tmp_path / 'runtime_state.json')
    assert db.read_bytes() == before
    assert not (tmp_path / 'paper/arbitrage.sqlite3').exists()


def test_missing_explicit_path_cannot_create_empty_database_beside_history(tmp_path):
    with sqlite3.connect(tmp_path/'arbitrage.sqlite3') as db:
        db.execute('CREATE TABLE history(net REAL)')
    values = environment(tmp_path, DB_PATH=str(tmp_path/'paper/arbitrage.sqlite3'))
    with pytest.raises(ValueError, match='CONFIGURED_DB_MISSING'):
        prepare_environment(values, tmp_path)
    assert not (tmp_path/'paper/arbitrage.sqlite3').exists()


def test_two_databases_require_explicit_selection(tmp_path):
    (tmp_path/'paper').mkdir()
    for path in (tmp_path/'arbitrage.sqlite3', tmp_path/'paper/arbitrage.sqlite3'):
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE history(net REAL)')
    with pytest.raises(ValueError, match='AMBIGUOUS'):
        prepare_environment(environment(tmp_path), tmp_path)


def test_relative_legacy_paths_follow_existing_database_directory(tmp_path):
    with sqlite3.connect(tmp_path/'arbitrage.sqlite3') as db:
        db.execute('CREATE TABLE history(net REAL)')
    values = environment(tmp_path, DB_PATH='arbitrage.sqlite3', RUNTIME_STATE_PATH='runtime_state.json')
    prepare_environment(values, tmp_path)
    assert values['DB_PATH'] == str(tmp_path/'arbitrage.sqlite3')
    assert values['RUNTIME_STATE_PATH'] == str(tmp_path/'runtime_state.json')


def test_root_runtime_without_database_cannot_silently_start_empty(tmp_path):
    (tmp_path/'runtime_state.json.paper_controls.json').write_text('{"paused":true}')
    with pytest.raises(ValueError, match='OLD_DATA_NOT_EMPTY'):
        prepare_environment(environment(tmp_path), tmp_path)


def test_database_outside_persistent_volume_is_rejected(tmp_path):
    with pytest.raises(ValueError, match='OUTSIDE_VOLUME'):
        prepare_environment(environment(tmp_path, DB_PATH='/app/arbitrage.sqlite3'), tmp_path)


def test_database_parent_traversal_is_rejected(tmp_path):
    with pytest.raises(ValueError, match='OUTSIDE_VOLUME'):
        prepare_environment(environment(tmp_path, DB_PATH=str(tmp_path)+'/../outside.sqlite3'), tmp_path)


def test_volume_directory_is_not_accepted_as_a_database(tmp_path):
    with pytest.raises(ValueError, match='DATABASE_NOT_FILE'):
        prepare_environment(environment(tmp_path, DB_PATH=str(tmp_path)), tmp_path)


def test_legacy_database_symlink_cannot_escape_volume(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + '-outside.sqlite3')
    with sqlite3.connect(outside) as db:
        db.execute('CREATE TABLE history(net REAL)')
    (tmp_path/'arbitrage.sqlite3').symlink_to(outside)
    with pytest.raises(ValueError, match='SYMLINK'):
        prepare_environment(environment(tmp_path), tmp_path)


def test_railway_new_database_and_redeploy_preserve_history_and_paths(tmp_path):
    values = environment(tmp_path)
    data = prepare_environment(values, tmp_path)
    db = data / "arbitrage.sqlite3"
    assert values["DB_PATH"] == str(db)
    assert values["RUNTIME_STATE_PATH"] == str(data / "runtime_state.json")
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE history(value TEXT)")
        c.execute("INSERT INTO history VALUES('preserve')")
    restart = environment(tmp_path)
    restart["PAPER_SERVER_FRESH_START"] = "false"
    assert prepare_environment(restart, tmp_path) == data
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT value FROM history").fetchone()[0] == "preserve"


@pytest.mark.parametrize("change,reason", [
    ({"RAILWAY_VOLUME_MOUNT_PATH": ""}, "VOLUME_REQUIRED"),
    ({"PAPER_SERVER_FRESH_START": "false"}, "CONFIRMATION"),
    ({"LIVE_ENABLED": "true"}, "LIVE_FLAGS"),
])
def test_railway_refuses_wrong_volume_live_or_unconfirmed_fresh_start(tmp_path, change, reason):
    values = environment(tmp_path)
    values.update(change)
    with pytest.raises(ValueError, match=reason):
        prepare_environment(values, tmp_path)
    assert not (tmp_path / "paper/arbitrage.sqlite3").exists()


def test_missing_database_with_old_state_is_not_reset(tmp_path):
    (tmp_path / "paper").mkdir()
    state = tmp_path / "paper/runtime_state.json"
    state.write_text('{"preserve":true}')
    with pytest.raises(ValueError, match="NOT_EMPTY"):
        prepare_environment(environment(tmp_path), tmp_path)
    assert json.loads(state.read_text()) == {"preserve": True}


def test_persisted_watchlist_restores_instead_of_default(tmp_path):
    values = environment(tmp_path)
    data = prepare_environment(values, tmp_path)
    routes = default_routes("reference", 7)
    (data / "watchlist.json").write_text(json.dumps(dict(routes=routes)))
    restart = environment(tmp_path)
    prepare_environment(restart, tmp_path)
    assert json.loads(restart["DEX_VIRTUAL_ROUTES_JSON"]) == routes


def test_automatic_watchlist_written_once_and_no_secret_in_file(monkeypatch, tmp_path):
    import app.public_client_factory as factory
    import app.jupiter_research as jupiter
    import app.build_virtual_watchlist as builder
    values = environment(tmp_path, JUPITER_API_KEY="secret-placeholder", DEX_VIRTUAL_WATCHLIST_PRESET="channel")
    data = prepare_environment(values, tmp_path)
    calls = []

    async def build(*args):
        calls.append("build")
        return {"bybit": object()}

    async def close(*args):
        calls.append("close")

    async def generate(routes, clients, provider, seeds):
        return routes, [dict(base="SOL", status="CONFIGURED", venue="bybit")]

    monkeypatch.setattr(factory, "build", build)
    monkeypatch.setattr(factory, "close", close)
    monkeypatch.setattr(builder, "generate", generate)
    asyncio.run(expand_watchlist(values, data))
    assert "secret-placeholder" not in (data / "watchlist.json").read_text()
    asyncio.run(expand_watchlist(values, data))
    assert calls == ["build", "close"]


def test_automatic_watchlist_failure_is_not_cached_forever(monkeypatch, tmp_path):
    import app.public_client_factory as factory
    import app.build_virtual_watchlist as builder
    values = environment(tmp_path, JUPITER_API_KEY="placeholder", DEX_VIRTUAL_WATCHLIST_PRESET="channel")
    data = prepare_environment(values, tmp_path)

    async def build(*args):
        return {"bybit": object()}

    async def close(*args):
        pass

    async def generate(routes, *args):
        return routes, [dict(base="SOL", status="SKIPPED", reason="JUPITER_HTTP_429")]

    monkeypatch.setattr(factory, "build", build)
    monkeypatch.setattr(factory, "close", close)
    monkeypatch.setattr(builder, "generate", generate)
    asyncio.run(expand_watchlist(values, data))
    assert not (data / "watchlist.json").exists()
    assert (data / "arbitrage.sqlite3").is_file()


def test_explicit_watchlist_refresh_extends_existing_routes(monkeypatch, tmp_path):
    import app.public_client_factory as factory
    import app.build_virtual_watchlist as builder
    values = environment(tmp_path, JUPITER_API_KEY="placeholder", DEX_VIRTUAL_WATCHLIST_PRESET="channel")
    data = prepare_environment(values, tmp_path)
    routes = json.loads(values["DEX_VIRTUAL_ROUTES_JSON"])
    (data / "watchlist.json").write_text(json.dumps(dict(routes=routes, revision="1")))
    values["DEX_VIRTUAL_WATCHLIST_REVISION"] = "2"

    async def build(*args):
        return {"bybit": object()}

    async def close(*args):
        pass

    async def generate(existing, *args):
        assert existing == routes
        return existing + [dict(existing[0], cex_venue="mexc")], [dict(base="SOL", status="CONFIGURED")]

    monkeypatch.setattr(factory, "build", build)
    monkeypatch.setattr(factory, "close", close)
    monkeypatch.setattr(builder, "generate", generate)
    asyncio.run(expand_watchlist(values, data))
    saved = json.loads((data / "watchlist.json").read_text())
    assert saved["revision"] == "2" and saved["routes"][0] == routes[0]
    assert len(saved["routes"]) == 2
