"""Two independent model portfolios in one bot and one audit database."""
import copy
import json
from html import escape
import aiosqlite
from .cex_dex_paper import Engine, Cycle
from .dex_virtual_paper_source import Source, route_key
from .dex_signal_focus import Signals

LABELS = {"dex_channel": "Канал → MEXC", "dex_bybit": "Автопоиск → Bybit"}


async def require_monitoring(db_path, enabled):
    if enabled:
        return
    async with aiosqlite.connect(db_path) as d:
        for suffix in ("channel", "bybit"):
            table = "cex_dex_virtual_paper_" + suffix
            async with d.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)) as c:
                if not await c.fetchone():
                    continue
            async with d.execute("SELECT COUNT(*) FROM " + table + " WHERE status!='CLOSED'") as c:
                if (await c.fetchone())[0]:
                    raise ValueError("DEX_OPEN_EXPERIMENT_REQUIRES_DEX_DUAL_PAPER_ENABLED")


async def build(bundle, runtime, db_path, state_path, notify, allowed):
    original = bundle.dex_paper.source
    engines = {}
    for name, venue, signals_only in (("dex_channel", "mexc", True), ("dex_bybit", "bybit", False)):
        routes = [dict(copy.deepcopy(r), notional_usd=20, max_notional_usd=20)
                  for r in original.routes if r["cex_venue"] == venue and r.get("mode") == "reference"]
        if not routes:
            raise ValueError("DEX_EXPERIMENT_ROUTES_REQUIRED:" + venue)
        source = Source(original.provider, routes, original.public, original.fs, original.private, original.clock)
        source.focus, source.signals_only, source.entry_venues = True, signals_only, {venue}
        source.signals = Signals(state_path + "." + name + ".signals.json", source.clock)
        engine = Engine(db_path, source, capital=50, max_seconds=900, virtual=True,
                        namespace="channel" if signals_only else "bybit", clock=source.clock)
        await engine.init()
        settings = dict(starting_capital=50, notional=20, venue=venue, signals_only=signals_only)
        if engine.state.get("experiment_config", settings) != settings:
            raise ValueError("DEX_EXPERIMENT_CONFIG_CHANGED:" + name)
        if "experiment_config" not in engine.state:
            engine.state["experiment_config"] = settings
            async with aiosqlite.connect(db_path) as d:
                c = await d.execute(engine.sql("UPDATE cex_dex_paper_state SET version=version+1,payload=? WHERE id=1 AND version=?"),
                                    (json.dumps(engine.state), engine.version))
                if c.rowcount != 1:
                    raise ValueError("DEX_EXPERIMENT_CONCURRENT_WRITER")
                await d.commit()
            engine.version += 1
        engine.budget = lambda e=engine: max(0, e.capital + e.state.get("realized_net", 0))
        engine.allow_open = lambda p, e=engine, n=name: runtime.enabled.get(n, False) and allowed(p) and daily_open_allowed(e)
        source.blocked_until = lambda r, e=engine: e.state.get("route_cooldowns", {}).get(json.dumps(route_key(r), separators=(",", ":")), 0)
        source.consumed_signal = lambda r, e=engine: e.state.get("consumed_signals", {}).get(json.dumps(route_key(r), separators=(",", ":")), 0)

        async def closed(p, e=engine, label=LABELS[name]):
            e.source.signals.closed(p)
            await notify(p["symbol"], p["net"], label + " • " + ("LONG" if p["cex_side"] == "buy" else "SHORT") + " • " + p["exit_reason"])

        engine.on_closed = closed
        service = Cycle(engine, routes)
        service.interval = 5
        bundle.runtime.add(name, service)
        runtime.enabled.setdefault(name, False)
        engines[name] = engine
    return engines


def daily_open_allowed(engine):
    state = engine.state
    return engine.budget() > 0 and (state.get("day") != int(engine.clock() // 86400)
                                  or state.get("day_net", 0) > -engine.capital * .05)


def render(engines, enabled):
    out = ["<b>Сравнение CEX↔DEX · виртуальные деньги</b>",
           "У каждого режима свой старт $50 и целевая сделка $20. Цены и расходы берутся из свежих котировок; reference без хеджа."]
    for name, e in engines.items():
        s = e.state
        count = s.get("closed_count", 0)
        floating = sum(p.get("net", 0) for p in e.positions.values())
        out.append(f"<b>{LABELS[name]}</b> · {'🟢 входы включены' if enabled.get(name) else '⚫ входы выключены'}\n"
                   f"Баланс после закрытий: ${e.budget():.4f} · свободно: ${max(0, e.budget()-e.used_capital):.2f}\n"
                   f"Закрытый NET: {s.get('realized_net',0):+.4f} USD · текущий NET позиций: {floating:+.4f} USD\n"
                   f"Сделок: {count} · прибыльных: {s.get('wins',0)} · позиций: {len(e.positions)}\n"
                   f"Просадка по закрытым сделкам: ${s.get('closed_drawdown',0):.4f}\n"
                   f"Маршрутов: {len(e.source.routes)} · проверок: {s.get('checked_count',0)}\n"
                   f"Последняя проверка: {escape(s.get('last_decision','ещё не было'))}")
        if not daily_open_allowed(e):
            out.append("Новые входы остановлены: дневной лимит потерь 5% собственного стартового капитала или недостаточный баланс.")
        if name == "dex_channel":
            reader = getattr(e, "channel_reader", None)
            out.append("Чтение канала: " + escape(reader.status if reader else "публичное чтение не включено; доступны пересылки"))
    out.append("Выключение запрещает новые входы, открытые позиции продолжают сопровождаться. Старый общий Paper сохраняется отдельно. Разные токены и площадки влияют на сравнение.")
    return "\n\n".join(out)
