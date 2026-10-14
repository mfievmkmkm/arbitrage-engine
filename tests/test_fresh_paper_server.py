import json
import sqlite3

import pytest

from app.configure_virtual_paper import default_routes
from app.init_paper_server import initialize


def setup(tmp_path):
    env = tmp_path / "bot.env"
    env.write_text("BOT_TOKEN=placeholder\nADMIN_ID=123\nZEROX_API_KEY=placeholder\nLIVE_ENABLED=false\nDEX_VIRTUAL_PAPER_ENABLED=true\nDEX_VIRTUAL_ROUTES_JSON=" + json.dumps(default_routes("reference")) + "\n")
    data = tmp_path / "data"
    data.mkdir()
    return env, data / "arbitrage.sqlite3"


def test_fresh_database_is_explicit_and_never_reinitialized(tmp_path):
    env, db = setup(tmp_path)
    assert initialize(env, db) == db
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT mode FROM paper_experiment_origin").fetchone()[0] == "EXPLICIT_FRESH_VIRTUAL_PAPER"
    original = db.read_bytes()
    with pytest.raises(ValueError, match="NOT_EMPTY"):
        initialize(env, db)
    assert db.read_bytes() == original


@pytest.mark.parametrize("name", ["runtime_state.json", "arbitrage.sqlite3-wal", "other.db"])
def test_fresh_refuses_existing_state_and_sidecars(tmp_path, name):
    env, db = setup(tmp_path)
    (db.parent / name).write_text("preserve")
    with pytest.raises(ValueError, match="NOT_EMPTY"):
        initialize(env, db)
    assert not db.exists()


@pytest.mark.parametrize("setting,reason", [("LIVE_ENABLED=true", "LIVE_FLAGS"), ("ADMIN_ID=0", "ADMIN_ID"), ("DEX_VIRTUAL_ROUTES_JSON=[]", "ROUTES"), ("ZEROX_API_KEY=", "ZEROX_API_KEY")])
def test_fresh_rejects_invalid_configuration_before_creating_db(tmp_path, setting, reason):
    env, db = setup(tmp_path)
    env.write_text(env.read_text() + setting + "\n")
    with pytest.raises(ValueError, match=reason):
        initialize(env, db)
    assert not db.exists()


def test_fresh_initialization_has_no_model_positions_or_imported_pnl(tmp_path):
    import asyncio
    from app.cex_dex_paper import Engine
    from test_dex_virtual_paper import source
    env, db = setup(tmp_path)
    initialize(env, db)

    async def go():
        s, *_ = source("reference")
        e = Engine(db, s, capital=50, clock=s.clock, virtual=True)
        await e.init()
        assert not e.positions and e.used_capital == 0 and e.budget() == 50
        assert e.state.get("realized_net", 0) == 0
    asyncio.run(go())
