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


async def build(bundle, runtime, db_path, state_path, notify, allowed, capital=250, notional=100, notify_open=None):
    original = bundle.dex_paper.source
    engines = {}
    for name, venue, signals_only in (("dex_channel", "mexc", True), ("dex_bybit", "bybit", False)):
        routes = [dict(copy.deepcopy(r), notional_usd=notional, max_notional_usd=notional)
                  for r in original.routes if r["cex_venue"] == venue and r.get("mode") == "reference"]
        if not routes:
            raise ValueError("DEX_EXPERIMENT_ROUTES_REQUIRED:" + venue)
        source = Source(original.provider, routes, original.public, original.fs, original.private, original.clock, max_notional=300)
        source.focus, source.signals_only, source.entry_venues = True, signals_only, {venue}
        source.adaptive = True
        source.signals = Signals(state_path + "." + name + ".signals.json", source.clock)
        engine = Engine(db_path, source, capital=capital, max_seconds=900, virtual=True,
                        namespace="channel" if signals_only else "bybit", clock=source.clock)
        await engine.init()
        settings = dict(starting_capital=capital, notional=notional, venue=venue, signals_only=signals_only)
        previous = engine.state.get("experiment_config")
        migration = previous == dict(starting_capital=50, notional=20, venue=venue, signals_only=signals_only) and capital == 250 and notional == 100
        if previous and previous != settings and not migration:
            raise ValueError("DEX_EXPERIMENT_CONFIG_CHANGED:" + name)
        if previous != settings:
            if migration:
                engine.state.setdefault("capital_changes", []).append(dict(ts=source.clock(), amount=200, reason="VIRTUAL_TOPUP_50_TO_250_NOT_PROFIT", old=previous))
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
        if notify_open:
            async def opened(p, label=LABELS[name]):
                await notify_open(p, label)
            engine.on_opened = opened
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
    out = ["📊 <b>ДВА PAPER-РЕЖИМА</b>",
           "У каждого свой виртуальный капитал и размер сделки с ограничением по ликвидности. Балансы и результаты независимы."]
    for name, e in engines.items():
        s = e.state
        count = s.get("closed_count", 0)
        floating = sum(p.get("net", 0) for p in e.positions.values())
        out.append(f"{'📨' if name == 'dex_channel' else '🔎'} <b>{LABELS[name]}</b>\n"
                   f"{'🟢 Новые входы включены' if enabled.get(name) else '⚪ Новые входы выключены'}\n"
                   f"Капитал ${e.capital:.0f} · сделка до ${s['experiment_config']['notional']:.0f}\n"
                   f"Баланс ${e.budget():.2f} · доступно ${max(0,e.budget()-e.used_capital):.2f}\n"
                   f"Результат закрытых: <b>{s.get('realized_net',0):+.4f} USD</b>\n"
                   f"Открыто {len(e.positions)} · текущий NET {floating:+.4f} USD\n"
                   f"Закрыто {count} · прибыльных {s.get('wins',0)} · проверок {s.get('checked_count',0)}\n"
                   f"Рынков {len(e.source.routes)} · {escape(reason_text(s.get('last_decision','')))}")
        if not daily_open_allowed(e):
            out.append("Новые входы остановлены: дневной лимит потерь 5% собственного стартового капитала или недостаточный баланс.")
        if name == "dex_channel":
            reader = getattr(e, "channel_reader", None)
            out.append("Чтение канала: " + escape(reader.status if reader else "публичное чтение не включено; доступны пересылки"))
            if reader:
                m = reader.metrics
                out.append(f"Постов-сигналов прочитано: {m['received']} · принято: {m['accepted']}\nПоследний: {escape(m['last'][:180])}")
    out.append("Выбери режим ниже для сделок, причин пропусков и списка рынков. Кошелёк и реальные ордера не используются. Отключение входов сохраняет сопровождение открытых позиций.")
    return "\n\n".join(out)


REASONS = {
    "DEX_CHANNEL_SIGNAL_REQUIRED": "Ждём свежий подходящий сигнал канала",
    "DEX_CHANNEL_SIGNAL_ALREADY_USED": "Этот сигнал уже использован",
    "DEX_REFERENCE_REWARD_TOO_SMALL": "После расходов недостаточно потенциальной прибыли",
    "DEX_MODEL_EDGE_LOW": "Недостаточный NET после расходов",
    "DEX_VIRTUAL_AMOUNT_BELOW_NATIVE_STEP": "Сумма после округления меньше минимального шага биржи",
    "DEX_REFERENCE_BOOK_TOO_WIDE": "Слишком широкий разрыв покупки и продажи в стакане",
    "DEX_REFERENCE_ROUNDTRIP_TOO_COSTLY": "Обратный выход слишком дорогой",
    "DEX_REFERENCE_CONFIRMATION_REQUIRED": "Проверяем подтверждение второй котировкой",
    "DEX_SIGNAL_DIRECTION_CHANGED": "Рынок больше не подтверждает направление сигнала",
    "DEX_SIGNAL_LOSS_COOLDOWN": "Пауза по токену после убытка",
    "DEX_PAPER_OPEN": "Виртуальная позиция открыта",
    "RECOVERY_BOOK_SLIPPAGE_TOO_HIGH": "Проскальзывание стакана выше лимита — подбираем меньшую сумму",
    "RECOVERY_LIQUIDITY_LOW": "Недостаточно глубины стакана — подбираем меньшую сумму",
    "RECOVERY_BOOK_STALE": "Стакан устарел — вход пропущен",
    "JUPITER_PRICE_IMPACT_LIMIT": "Размер слишком сильно влияет на цену DEX",
    "RISK_OR_VENUE_DISABLED": "Вход отключён или достигнут лимит риска",
    "SHARED_CAPITAL_LOW": "Недостаточно свободного виртуального баланса",
}


def reason_text(reason):
    return REASONS.get(reason, "Ещё нет проверок" if not reason else "Вход пропущен: " + reason)


def render_mode(name, engine, enabled):
    s = engine.state
    out = [f"{'📨' if name == 'dex_channel' else '🔎'} <b>{LABELS[name]}</b>",
           f"{'🟢 Входы включены' if enabled else '⚪ Входы выключены'} · только виртуальные деньги",
           f"Выделенный капитал: ${engine.capital:.0f}\nЦелевая сделка: ${s['experiment_config']['notional']:.0f}\nБаланс после закрытий: ${engine.budget():.4f}\nВременный резерв проверки / позиции: ${engine.used_capital:.2f}",
           f"<b>Результаты</b>\nЗакрытый NET: {s.get('realized_net',0):+.4f} USD\nЗакрыто: {s.get('closed_count',0)} · прибыльных: {s.get('wins',0)}\nПросадка по закрытым: ${s.get('closed_drawdown',0):.4f}"]
    if s.get("capital_changes"):
        out.append("Виртуальный капитал увеличен с $50 до $250. Добавленные $200 не считаются прибылью; прежний NET сохранён.")
    if engine.positions:
        out.append("<b>Открытые позиции</b>")
        for p in engine.positions.values():
            out.append(f"{escape(p['symbol'])} · {'LONG' if p['cex_side']=='buy' else 'SHORT'}\nРазмер: ${p['base_qty']*p['entry_price']:.2f} · NET сейчас: {p['net']:+.4f} USD\nВозраст: {max(0,engine.clock()-p['opened_at']):.0f} с · {escape(p.get('data_reason','OK'))}")
    else:
        out.append("<b>Открытых позиций нет</b>")
    out.append("<b>Последняя проверка</b>\n" + escape(reason_text(s.get("last_decision", ""))))
    route = s.get("last_route")
    if route:
        out.append(f"{escape(route['symbol'])} · {max(0,engine.clock()-route['ts']):.0f} с назад")
    d = s.get("last_failure_detail", {})
    if "entry_edge" in d:
        out.append(f"Потенциальный NET: {d['entry_edge']:+.4f} USD\nNET немедленного обратного выхода: {d['immediate_net']:+.4f} USD")
    if d.get("size_attempts"):
        out.append("<b>Подбор суммы</b>\n" + "\n".join(f"${a['notional_usd']:.2f} · {escape(reason_text(a['reason']))}" for a in d["size_attempts"]))
    if d.get("amount_step") is not None:
        out.append(f"Шаг биржи: {d['amount_step']:.8g} контрактов · запрошено {d['requested_contracts']:.8g}")
    reader = getattr(engine, "channel_reader", None)
    if reader:
        out.append("<b>Сигналы канала</b>\n" + escape(reader.status) + f"\nПрочитано {reader.metrics['received']} · принято {reader.metrics['accepted']}\n" + escape(reader.metrics['last'][:250]))
    history = s.get("signal_checks", [])[-5:]
    if history:
        out.append("<b>Последние проверки сигналов</b>\n" + "\n".join(f"{escape(h['symbol'])} · {max(0,engine.clock()-h['ts']):.0f} с назад · {escape(reason_text(h['reason']))}" for h in history))
    reasons = sorted(s.get("decision_counts",{}).items(), key=lambda x:-x[1])[:4]
    if reasons:
        out.append("<b>Основные причины</b>\n" + "\n".join(f"{escape(reason_text(r))}: {n}" for r,n in reasons))
    out.append("<b>Рынки</b>\n" + escape(", ".join(r['base'] for r in engine.source.routes)))
    out.append(f"Сделка до ${s['experiment_config']['notional']:.0f}: фактический размер зависит от шага биржи, стакана и Max Size сигнала. Ожидаемый NET — прогноз после расходов, а не заработанная прибыль.")
    discovery = getattr(engine, "discovery", None)
    if discovery:
        out.append("Подбор маршрутов: " + escape(discovery.status))
    return "\n\n".join(out)
