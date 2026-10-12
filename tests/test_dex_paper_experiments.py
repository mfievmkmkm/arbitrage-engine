import asyncio
import sqlite3
from types import SimpleNamespace
import pytest
from app.cex_dex_paper import Engine
from app.dex_paper_experiments import build, render, daily_open_allowed, require_monitoring
from app.strategy_runtime import StrategyRuntime
from app.secondary_strategy_runtime import SecondaryRuntime


def test_independent_portfolios_restart_toggles_and_exports(tmp_path, monkeypatch):
    from test_dex_virtual_paper import source
    monkeypatch.delenv("DEX_VIRTUAL_ENTRY_VENUES", raising=False)
    monkeypatch.delenv("DEX_REFERENCE_FOCUS", raising=False)
    monkeypatch.delenv("DEX_CHANNEL_SIGNALS_ONLY", raising=False)
    async def go():
        s, r, public, provider, now = source("reference")
        s.routes.append(dict(r, cex_venue="mexc"))
        s.public["mexc"] = public
        db = tmp_path / "db.sqlite3"
        old = Engine(db, s, virtual=True, clock=s.clock)
        await old.init()
        runtime = StrategyRuntime()
        sr = SecondaryRuntime(runtime, None, db)
        bundle = SimpleNamespace(dex_paper=old, runtime=sr)
        notices = []
        async def notify(*args):notices.append(args)
        engines = await build(bundle, runtime, str(db), str(tmp_path / "runtime"), notify, lambda p: True)
        channel, bybit = engines["dex_channel"], engines["dex_bybit"]
        assert runtime.enabled["spot_futures"]  # existing functions and switches remain
        assert not runtime.enabled["dex_channel"] and not runtime.enabled["dex_bybit"]
        runtime.enabled["dex_bybit"] = True
        assert channel.budget() == bybit.budget() == 50
        assert channel.source.signals_only and not bybit.source.signals_only
        assert {r["cex_venue"] for r in channel.source.routes} == {"mexc"}
        assert {r["cex_venue"] for r in bybit.source.routes} == {"bybit"}
        assert all(r["notional_usd"] == 20 for e in engines.values() for r in e.source.routes)
        assert (await channel.source.entry(channel.source.routes[0]))["reason"] == "DEX_CHANNEL_SIGNAL_REQUIRED"
        await bybit.cycle(bybit.source.routes[0])
        assert bybit.state["last_decision"] == "DEX_REFERENCE_CONFIRMATION_REQUIRED"
        now[0] += 5
        await bybit.cycle(bybit.source.routes[0])
        assert bybit.positions and not channel.positions and not old.positions
        await require_monitoring(db, True)
        with pytest.raises(ValueError):await require_monitoring(db, False)
        # Disable entries; open positions still exit and credit only their own balance.
        runtime.enabled["dex_bybit"] = False
        public.bid = 1.0
        now[0] += 5
        closed = await bybit.cycle(None, False)
        assert closed and closed[0]["net"] > 0
        assert bybit.budget() > 50 and channel.budget() == 50
        assert notices[0][2].startswith("Автопоиск → Bybit")
        assert "50.0000" in render(engines, runtime.enabled)
        await require_monitoring(db, False)
        rebuilt = await build(bundle, runtime, str(db), str(tmp_path / "runtime"), notify, lambda p: True)
        assert rebuilt["dex_bybit"].budget() == bybit.budget()
        assert rebuilt["dex_channel"].budget() == 50
        assert rebuilt["dex_bybit"].state["wins"] == 1
        with sqlite3.connect(db) as c:
            assert c.execute("SELECT kind FROM ledger").fetchone()[0] == "CEX_DEX_BYBIT_EXPERIMENT_NET"
            assert c.execute("SELECT COUNT(*) FROM cex_dex_virtual_paper").fetchone()[0] == 0
        from app.audit_export import TABLES
        assert TABLES["DEX channel trades"] == "cex_dex_virtual_paper_channel"
        assert TABLES["DEX Bybit trades"] == "cex_dex_virtual_paper_bybit"
        bybit.state.update(day=int(now[0] // 86400), day_net=-2.5)
        assert not daily_open_allowed(bybit) and daily_open_allowed(channel)
        now[0] += 86400
        assert daily_open_allowed(bybit)
    asyncio.run(go())


def test_signal_consumption_and_loss_cooldown_survive_missing_callback(tmp_path):
    from test_dex_virtual_paper import source
    async def go():
        s, r, public, provider, now = source("reference")
        s.routes.append(dict(r, cex_venue="mexc"))
        s.public["mexc"] = public
        db = tmp_path / "crash.db"
        old = Engine(db, s, virtual=True, clock=s.clock)
        await old.init()
        runtime = StrategyRuntime()
        bundle = SimpleNamespace(dex_paper=old, runtime=SecondaryRuntime(runtime, None, db))
        async def notify(*args):pass
        engines = await build(bundle, runtime, str(db), str(tmp_path / "runtime"), notify, lambda p: True)
        channel = engines["dex_channel"]
        runtime.enabled["dex_channel"] = True
        route = channel.source.routes[0]
        text = "SHORT? #X\n#ETHEREUM\n" + route["asset"]
        channel.source.signals.add(text, [route], now[0], "MexcDexSpreadTracker")
        await channel.cycle(route)
        now[0] += 5
        await channel.cycle(route)
        assert channel.positions
        # Simulate process loss between committed close and notification callback.
        channel.on_closed = None
        public.bid = 1.08
        now[0] += 5
        closed = await channel.cycle(None, False)
        assert closed and closed[0]["net"] < 0 and closed[0]["exit_reason"] == "NET_STOP"
        assert channel.budget() < 50 and engines["dex_bybit"].budget() == 50
        recovered = await build(bundle, runtime, str(db), str(tmp_path / "runtime"), notify, lambda p: True)
        channel = recovered["dex_channel"]
        assert channel.source.consumed_signal(route) == 100
        assert (await channel.source.entry(route))["reason"] == "DEX_SIGNAL_LOSS_COOLDOWN"
        assert channel.state["closed_drawdown"] > 0
    asyncio.run(go())
