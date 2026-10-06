import asyncio, logging, tempfile
from html import escape
from pathlib import Path
from datetime import datetime, timezone
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    FSInputFile,
)
from .config import config
from .db import Diary
from .engine import Scanner
from .paper import PaperEngine
from .reports import build_replay_report
from .risk import RiskGuard
from .private_registry import PrivateRegistry
from .private_order_reader import Reader as PrivateOrderReader
from .private_funding_reader import (
    Reader as PrivateFundingReader,
    PairReader as PairFundingReader,
)
from .live_market_reader import Reader as LiveMarketReader
from .live_monitor import Monitor as LiveMonitor
from .live_monitor_view import (
    status as monitor_status,
    positions as monitor_positions,
    incidents as monitor_incidents,
)
from .runtime_state import RuntimeTrade
from .private_factory import build_private_readers, close_clients
from .live_bootstrap import bootstrap
from .startup_runtime import evaluate as evaluate_runtime_startup
from .runtime_store import RuntimeStore
from .startup_report import render as render_startup
from .preflight import check as preflight_check, render as render_preflight
from .paper_campaign import status as campaign_status, render as render_campaign
from .live_supervisor import LiveSupervisor
from .persistent_stop import Stop
from .live_trade_store import Store as LiveTradeStore
from .live_startup_recovery import recover as durable_recover
from .ledger_store import init as ledger_init, add as ledger_add
from .paper_ledger import restore as restore_ledger
from .secondary_bootstrap import build_bundle
from .audit_export import build as build_audit_export
from .replay import walk_forward
from .strategy_toggle_store import Store as StrategyToggleStore
from .venue_mode_store import Store as VenueModeStore
from .venue_controller import Controller as VenueController
from .tg_venue_keyboard import list_keyboard as venue_keyboard, control as venue_control
from .tg_venue_detail import render as venue_detail
from .live_control_view import render as render_live_control
from .live_commands import stop as command_stop, resume as command_resume
from .resume_evidence import collect as collect_resume
from .live_heartbeat import evaluate as heartbeat_eval
from types import SimpleNamespace
from .strategy_runtime import StrategyRuntime
from .multi_strategy_view import render as render_strategies
from .strategy_diary import (
    init as strategy_diary_init,
    summary as strategy_diary_summary,
)
from .strategy_stats_view import render as render_strategy_stats
from .bankroll_ledger import Ledger
from .capital_view import render as render_capital
from .strategy_observation import row as strategy_row
from .strategy_diary import record as strategy_diary_record
from .tg_ui import main_menu, back_menu, live_menu
from .tg_dashboard import (
    home as render_home,
    opportunities as render_market,
    venues as render_venues,
)
from .tg_pages import strategies as render_strategy_console, dex as render_dex_console
from .tg_safe_edit import edit as safe_edit
from .tg_export_center import render as render_export_center
from .tg_incident_banner import render as render_incident_banner
from .market_console import render as render_market_console
from .tg_risk_center import render as render_risk_center
from .tg_system_center import render as render_system_center
from .tg_position_console import paper as render_positions
from .market_console import merged as merged_market
from .tg_market_keyboard import build as market_keyboard, selection_key
from .tg_market_detail import render as render_market_detail

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("arbitrage")
diary = Diary(config.db_path)
scanner = Scanner(
    config.exchanges,
    config.notional,
    config.max_age,
    config.universe_size,
    config.scan_batch_size,
    config.scan_concurrency,
    config.safety_buffer_pct,
    config.paper_max_seconds,
)
paper = PaperEngine(
    diary,
    config.paper_capital,
    config.max_paper_positions,
    config.paper_target_convergence,
    config.paper_trailing_drawdown,
    config.paper_max_seconds,
)
risk = RiskGuard(config.paper_capital, config.daily_stop_pct, config.max_engine_errors)
latest = []
dp = Dispatcher()
startup_text = "🚦 Startup ещё не выполнен"
private_clients = {}
live_supervisor = LiveSupervisor(config.max_engine_errors)
live_stop = None
strategy_runtime = StrategyRuntime()
bankroll = Ledger(config.paper_capital)
notification_bot = None
secondary = None
live_monitor = None
live_trades = []
durable = LiveTradeStore(config.db_path)
strategy_toggles = StrategyToggleStore(config.runtime_state_path + ".strategies.json")
strategy_runtime.enabled = strategy_toggles.load(strategy_runtime.enabled)
venue_controller = VenueController(
    VenueModeStore(config.runtime_state_path + ".venues.json"), config.exchanges
)


def menu():
    return main_menu()


def allowed(uid):
    return bool(config.admin_id and uid == config.admin_id)


def fmt_top():
    if not latest:
        return "Пока нет подходящих наблюдений."
    lines = ["🔎 ВОЗМОЖНОСТИ — PAPER DATA", "Оценка, не гарантированная прибыль.\n"]
    for x in latest[:8]:
        lines.append(
            f"{x['symbol']} | {x['buy']} LONG / {x['sell']} SHORT\nИсполнимый вход: {x['executable']:.2f}% | NET: {x['hypothetical_edge']:.2f}% | funding: {x.get('funding_pct',0):+.3f}%\n"
        )
    return "\n".join(lines)


def fmt_paper():
    if not paper.positions:
        return f"🧪 PAPER\nКапитал: ${paper.capital:.2f}\nАктивных позиций нет."
    lines = [
        f"🧪 PAPER • капитал ${paper.capital:.2f} • занято ${paper.used_capital:.2f}"
    ]
    for p in paper.positions.values():
        conv = (1 - p.current_spread / p.entry_spread) * 100 if p.entry_spread else 0
        lines.append(
            f"\n{p.symbol}\n🟢 {p.buy} / 🔴 {p.sell}\nВход: {p.entry_spread:.2f}% → сейчас: {p.current_spread:.2f}%\nСхождение: {conv:.1f}%\nЕсли закрыть сейчас: {p.current_net_usd:+.4f} USD\nМаксимум: {p.best_net_usd:+.4f} USD"
        )
    return "\n".join(lines)


async def text_for(s):
    if s == "home":
        banner = render_incident_banner(live_supervisor, live_stop)
        body = render_home(scanner, paper, risk, strategy_runtime, live_stop)
        return (banner + "\n\n" + body) if banner else body
    if s == "export":
        return render_export_center()
    if s == "risk":
        return render_risk_center(risk, live_supervisor, live_stop)
    if s == "status":
        return render_system_center(scanner, strategy_runtime)
    if s == "strategies":
        return render_strategy_console(strategy_runtime)
    if s == "strategy_stats":
        return render_strategy_stats(await strategy_diary_summary(config.db_path))
    if s == "capital":
        return render_capital(bankroll)
    if s == "dex":
        rows = strategy_runtime.top("cex_dex")
        if not rows:
            return (
                render_dex_console()
                + "\n\nДля price-research нужны ZEROX_API_KEY и DEX_RESEARCH_ROUTES_JSON."
            )
        out = ["⛓ DEX • индикативные цены • REAL/PAPER заблокированы"]
        for row in rows:
            q = row["quote"]
            out.append(
                escape(row["symbol"])
                + " • "
                + ("buy raw " + q["buy_amount_raw"] if q["ok"] else q["reason"])
            )
        return "\n".join(out)
    if s == "top":
        return render_market_console(strategy_runtime)
    if s == "paper":
        text = render_positions(paper)
        if secondary and secondary.sf_paper.positions:
            text += "\n\n🧪 SPOT ↔ FUTURES"
            for p in secondary.sf_paper.positions.values():
                text += f"\n{escape(p.base)} • {escape(p.exchange)} • NET {p.net:+.4f}$"
        return text
    if s == "replay":
        data = await diary.replay_trades()
        validation = walk_forward(data)
        if validation["status"] == "validated_split":
            tr = validation["train"]
            te = validation["test"]
            return f"🧪 REPLAY • хронологическая проверка\nTrain: {validation['train_size']} • NET {tr['net']:+.4f}$\nПараметры: target {tr['target']:.0%}, trailing {tr['trailing']:.0%}, {tr['seconds']} сек\nОтложенная выборка: {validation['test_size']}\nOOS NET: {te['net']:+.4f}$ • PF {te['profit_factor']:.2f}\nMax DD: {te['max_drawdown']:.4f}$\nПараметры автоматически не меняются."
        rows, report = await build_replay_report(diary)
        if not rows:
            return "🧠 REPLAY\nПока недостаточно закрытых paper-сделок."
        b = rows[0]
        return f"🧠 REPLAY • исследовательский\nСделок: {b['trades']}\nЛучший кандидат: target {b['target']*100:.0f}% / trailing {b['trailing']*100:.0f}% / {b['seconds']//60} мин\nNET: {b['net']:+.4f} USD\nWin rate: {b['win_rate']:.1f}%\nMax DD: {b['max_drawdown']:.4f} USD\n\n⚠️ In-sample: параметры автоматически не меняются."
    if s == "diary":
        count, last, best = await diary.summary()
        trades, pnl, wins = await diary.paper_stats()
        stamp = (
            datetime.fromtimestamp(last, timezone.utc).strftime("%d.%m %H:%M UTC")
            if last
            else "—"
        )
        return f"📔 ДНЕВНИК\nНаблюдений: {count}\nПоследнее: {stamp}\nЗакрыто paper: {trades}\nPaper NET: {pnl:+.4f} USD\nПрибыльных: {wins}"
    if s == "exchanges":
        health = scanner.health.snapshot()
        lines = []
        for x in scanner.ids:
            h = health.get(x, {})
            lines.append(
                f"{x}: {'🟢' if x in scanner.clients else '🔴'} • success {h.get('success_pct',0):.0f}% • {h.get('latency_ms','—')} ms"
            )
        return render_venues(scanner)
    if s == "risk":
        rs = risk.state
        return f"🛡 RISK CENTER\nСтатус: {'🛑 HALT' if rs.halted else '🟢 NORMAL'}\nПричина: {rs.reason or '—'}\nОшибок подряд: {rs.consecutive_errors}/{risk.max_errors}\nPaper PnL сегодня: {rs.paper_daily_pnl:+.4f} USD\nDaily stop: -{risk.bankroll*risk.daily_stop_pct/100:.2f} USD\nLIVE: заблокирован до private reconciliation"
    if s == "startup":
        return startup_text
    if s == "campaign":
        return render_campaign(await campaign_status(diary))
    if s == "live":
        summary = live_monitor.latest if live_monitor else None
        realized = (summary or {}).get("realized", {}).get("net", 0)
        return (
            render_live_control(live_supervisor, live_trades, realized, live_stop)
            + "\n\n"
            + monitor_status(summary)
        )
    if s == "live_positions":
        return monitor_positions(live_monitor.latest if live_monitor else None)
    if s == "incidents":
        return monitor_incidents(live_monitor.latest if live_monitor else None)
    if s == "status":
        stamp = (
            datetime.fromtimestamp(scanner.last_scan, timezone.utc).strftime(
                "%H:%M:%S UTC"
            )
            if scanner.last_scan
            else "—"
        )
        coverage = scanner.universe.coverage if scanner.universe else 0
        rs = risk.state
        return f"📡 СТАТУС\nUniverse: {coverage} рынков\nСканер: {'⏸' if scanner.paused else '🟢'}\nБирж: {len(scanner.clients)}\nПоследний цикл: {stamp}\nPaper: {len(paper.positions)}/{paper.max_positions}\nRisk: {'🛑 '+rs.reason if rs.halted else '🟢 OK'} • day {rs.paper_daily_pnl:+.4f}$\nLIVE: ОТКЛЮЧЕН"
    return "Неизвестный раздел"


@dp.message(CommandStart())
async def start(m: Message):
    if allowed(m.from_user.id):
        await m.answer(
            render_home(scanner, paper, risk, strategy_runtime, live_stop),
            reply_markup=menu(),
            parse_mode="HTML",
        )


@dp.message(
    Command(
        "top",
        "paper",
        "diary",
        "replay",
        "exchanges",
        "status",
        "risk",
        "startup",
        "campaign",
        "live",
        "live_stop",
        "live_resume",
        "live_positions",
        "incidents",
        "strategies",
        "strategy_stats",
        "capital",
        "dex",
        "export",
        "pause",
        "resume",
    )
)
async def commands(m: Message):
    if not allowed(m.from_user.id):
        return
    s = m.text.split()[0].lstrip("/").split("@")[0]
    if s == "export":
        await send_export(m)
        return
    if s in ("pause", "resume"):
        scanner.paused = s == "pause"
        s = "status"
    if s == "live_stop":
        command_stop(live_stop)
        s = "live"
    if s == "live_resume":
        ev = collect_resume(
            SimpleNamespace(
                safe=live_supervisor.restart_clean, reason="RESTART_UNSAFE"
            ),
            live_supervisor.private_verified,
            live_supervisor.unknown_orders,
            heartbeat_eval(0, 0, True, True),
            False,
        )
        command_resume(live_stop, ev, live_supervisor.kill)
        s = "live"
    await m.answer(await text_for(s), reply_markup=keyboard_for(s), parse_mode="HTML")


@dp.callback_query(
    F.data.in_(
        {
            "home",
            "top",
            "paper",
            "diary",
            "replay",
            "exchanges",
            "status",
            "risk",
            "startup",
            "campaign",
            "live",
            "live_stop",
            "live_resume",
            "live_positions",
            "incidents",
            "strategies",
            "strategy_stats",
            "capital",
            "dex",
            "export",
            "pause",
            "resume",
        }
    )
)
async def callbacks(q: CallbackQuery):
    if not allowed(q.from_user.id):
        await q.answer("Нет доступа", show_alert=True)
        return
    s = q.data
    if s == "export":
        await q.answer("Готовлю выгрузку")
        await send_export(q.message)
        return
    if s in ("pause", "resume"):
        scanner.paused = s == "pause"
        s = "status"
    if s == "live_stop":
        command_stop(live_stop)
        s = "live"
    if s == "live_resume":
        ev = collect_resume(
            SimpleNamespace(
                safe=live_supervisor.restart_clean, reason="RESTART_UNSAFE"
            ),
            live_supervisor.private_verified,
            live_supervisor.unknown_orders,
            heartbeat_eval(0, 0, True, True),
            False,
        )
        command_resume(live_stop, ev, live_supervisor.kill)
        s = "live"
    await safe_edit(q.message, await text_for(s), keyboard_for(s))
    await q.answer()


async def send_export(message):
    with tempfile.TemporaryDirectory(prefix="arbitrage-export-") as directory:
        workbook, archive = await build_audit_export(config.db_path, directory)
        await message.answer_document(
            FSInputFile(workbook),
            caption="Дневник: наблюдения, решения, позиции, исполнения и состояние восстановления. До 50 000 строк на таблицу.",
        )
        await message.answer_document(
            FSInputFile(archive), caption="Те же данные в CSV."
        )


def keyboard_for(screen):
    if screen == "home":
        return menu()
    if screen == "live":
        return live_menu(live_stop.stopped)
    if screen == "top":
        return market_keyboard(merged_market(strategy_runtime))
    if screen == "exchanges":
        return venue_keyboard(scanner.ids)
    if screen == "strategies":
        rows = [
            [
                InlineKeyboardButton(
                    text=("🟢 " if on else "⚫ ") + name,
                    callback_data="strategy:" + name,
                )
            ]
            for name, on in strategy_runtime.enabled.items()
        ]
        rows.append([InlineKeyboardButton(text="‹ Меню", callback_data="home")])
        return InlineKeyboardMarkup(inline_keyboard=rows)
    return back_menu()


@dp.callback_query(F.data.startswith("opp:"))
async def opportunity(q: CallbackQuery):
    if not allowed(q.from_user.id):
        await q.answer("Нет доступа", show_alert=True)
        return
    try:
        edge, name, row = next(
            x
            for x in merged_market(strategy_runtime)
            if selection_key(x[1], x[2]) == q.data.split(":")[1]
        )
    except (ValueError, IndexError, StopIteration):
        await q.answer("Список обновился — открой рынок снова", show_alert=True)
        return
    await safe_edit(q.message, render_market_detail(name, row), back_menu())
    await q.answer()


@dp.callback_query(F.data.startswith("strategy:"))
async def strategy_toggle(q: CallbackQuery):
    if not allowed(q.from_user.id):
        await q.answer("Нет доступа", show_alert=True)
        return
    name = q.data.split(":", 1)[1]
    if name not in strategy_runtime.enabled:
        return await q.answer("Неизвестная стратегия")
    if name == "cex_dex":
        return await q.answer(
            "DEX ожидает подключения и проверки провайдера", show_alert=True
        )
    strategy_runtime.enabled[name] = not strategy_runtime.enabled[name]
    strategy_toggles.save(strategy_runtime.enabled)
    await safe_edit(q.message, await text_for("strategies"), keyboard_for("strategies"))
    await q.answer()


@dp.callback_query(F.data.startswith("venue:") | F.data.startswith("vt:"))
async def venue_action(q: CallbackQuery):
    if not allowed(q.from_user.id):
        await q.answer("Нет доступа", show_alert=True)
        return
    parts = q.data.split(":")
    name = parts[1]
    if name not in scanner.ids:
        return await q.answer("Неизвестная площадка")
    if parts[0] == "vt":
        if len(parts) != 3 or parts[2] not in ("scan", "paper", "real"):
            return await q.answer("Неизвестный режим")
        modes, ok, reason = venue_controller.toggle(name, parts[2], False, False)
        if not ok:
            return await q.answer(
                "REAL заблокирован: нужна проверка площадки и LIVE acceptance",
                show_alert=True,
            )
    text = venue_detail(
        name, scanner.health.snapshot().get(name, {}), venue_controller.get(name)
    )
    await safe_edit(q.message, text, venue_control(name, venue_controller.get(name)))
    await q.answer()


async def notify_paper_close(symbol, net, reason):
    if notification_bot is None:
        return
    try:
        await notification_bot.send_message(
            config.admin_id,
            f"🧪 PAPER • позиция закрыта\n{escape(symbol)}\nNET по модели: {net:+.4f} USD\nПричина: {escape(reason)}",
            parse_mode="HTML",
        )
    except Exception:
        log.exception("Paper notification failed")


def paper_entry_reason(op):
    if not strategy_runtime.enabled["futures_futures"]:
        return "STRATEGY_DISABLED"
    if not all(
        venue_controller.get(v)["scan"] and venue_controller.get(v)["paper"]
        for v in (op["buy"], op["sell"])
    ):
        return "VENUE_PAPER_DISABLED"
    if not risk.can_open_paper():
        return "RISK_HALTED"
    if op["hypothetical_edge"] < config.paper_entry_edge:
        return "BELOW_ENTRY_EDGE"
    if not paper.can_open(op):
        return "CAPACITY_OR_DUPLICATE"
    return "ENTRY_ALLOWED"


async def scanning():
    global latest
    while True:
        try:
            if not scanner.paused:
                scanner.watch_routes = {
                    (p.symbol, p.buy, p.sell) for p in paper.positions.values()
                }
                scanner.scan_enabled = {
                    x for x in scanner.ids if venue_controller.get(x)["scan"]
                }
                quotes = await scanner.scan()
                latest = [x for x in quotes if x["hypothetical_edge"] > 0]
                strategy_runtime.update(
                    "futures_futures",
                    latest if strategy_runtime.enabled["futures_futures"] else [],
                )
                await strategy_diary_record(
                    config.db_path,
                    [
                        strategy_row({**x, "strategy": "futures_futures"})
                        for x in latest
                    ],
                )
                risk.on_success()
                await diary.record(quotes)
                closed = await paper.mark_and_exit(quotes)
                for p in closed:
                    risk.on_paper_close(p.current_net_usd)
                    bankroll.apply(p.current_net_usd)
                    await ledger_add(
                        config.db_path,
                        datetime.now(timezone.utc).timestamp(),
                        "PAPER_NET",
                        p.current_net_usd,
                        str(p.id),
                        note=p.symbol,
                    )
                    log.info("Paper close %s net=%s", p.symbol, p.current_net_usd)
                    await notify_paper_close(
                        p.symbol,
                        p.current_net_usd,
                        "Закрытие по политике выхода; подробности в дневнике",
                    )
                decisions = []
                for o in latest:
                    reason = paper_entry_reason(o)
                    p = await paper.open(o) if reason == "ENTRY_ALLOWED" else None
                    decisions.append(
                        {
                            **o,
                            "strategy": "futures_futures",
                            "action": "PAPER_OPEN" if p else "SKIP",
                            "reason": reason,
                        }
                    )
                    if p:
                        log.info("Paper open %s %s/%s", p.symbol, p.buy, p.sell)
                await diary.record_decisions(decisions)
                log.info(
                    "Cycle opportunities=%s paper=%s", len(latest), len(paper.positions)
                )
        except Exception:
            risk.on_error()
            log.exception("Scan failed")
        await asyncio.sleep(config.interval)


async def main():
    global startup_text, private_clients, secondary, live_trades, live_stop, notification_bot, live_monitor
    live_stop = Stop(config.runtime_state_path + ".stop.json")
    pf = preflight_check(config)
    if not pf.ok:
        raise RuntimeError(render_preflight(pf))
    if pf.warnings:
        log.warning("%s", render_preflight(pf).replace("\\n", " | "))
    secondary = None
    live_monitor = None
    private_clients = {}
    bot = None
    task = None
    try:
        Path(config.db_path).parent.mkdir(parents=True, exist_ok=True)
        await diary.init()
        await strategy_diary_init(config.db_path)
        await ledger_init(config.db_path)
        await durable.init()
        await paper.restore()
        await restore_ledger(config.db_path, bankroll, risk)
        readers, private_clients = build_private_readers()
        boot = await bootstrap(readers)
        runtime_store = RuntimeStore(config.runtime_state_path)
        recovery = await durable_recover(durable, runtime_store, diary, boot.ready)
        live_trades = recovery["trades"]
        intent_states = await diary.order_intent_states()
        startup = evaluate_runtime_startup(
            live_trades, boot.snapshot, False, intent_states
        )
        if recovery["active"] or recovery["actions"]:
            from .startup_runtime import StartupRuntime

            startup = StartupRuntime(
                False,
                "OBSERVATION",
                recovery["reason"],
                {"actions": recovery["actions"]},
            )
        live_supervisor.restart_clean = startup.safe and not recovery["active"]
        live_supervisor.private_verified = boot.ready
        live_supervisor.unknown_orders = bool(recovery["unknown_intents"])
        startup_text = render_startup(startup, boot.snapshot, live_trades)
        startup_text += (
            "\nИсполнение LIVE: заблокировано до release acceptance.\nБД LIVE: "
            + str(len(recovery["active"]))
            + " незавершённых • "
            + recovery["reason"]
        )
        log.info("%s", startup_text.replace("\n", " | "))
        await scanner.start()
        registry = PrivateRegistry()
        for name, reader in readers.items():
            registry.add(name, reader)
        order_readers = {
            name: PrivateOrderReader(name, client)
            for name, client in private_clients.items()
        }
        funding_readers = {
            name: PrivateFundingReader(name, client)
            for name, client in private_clients.items()
        }

        async def monitor_update(summary, new_incidents):
            global live_trades
            live_trades = [RuntimeTrade(**row) for row in summary["runtime_trades"]]
            if notification_bot:
                for item in new_incidents:
                    if item["severity"] in ("CRITICAL", "HIGH"):
                        try:
                            await notification_bot.send_message(
                                config.admin_id,
                                "🛑 Инцидент исполнения\n"
                                + escape(item["code"])
                                + "\nСделка: "
                                + escape(item["trade_id"] or "система")
                                + "\nНовые входы заблокированы. Подробности: /incidents",
                                parse_mode="HTML",
                            )
                        except Exception:
                            log.exception("Incident notification failed")
                for result in summary["closed"]:
                    try:
                        await notification_bot.send_message(
                            config.admin_id,
                            f"✅ Закрытие подтверждено private API\n{escape(result['trade_id'])}\nФактический NET: {result['net']:+.4f} USD",
                            parse_mode="HTML",
                        )
                    except Exception:
                        log.exception("Verified close notification failed")

        live_monitor = LiveMonitor(
            durable,
            runtime_store,
            diary,
            registry.snapshot,
            order_readers,
            live_supervisor,
            live_stop,
            market_reader=LiveMarketReader(
                scanner.clients, private_clients, config.live_max_book_age_ms / 1000
            ),
            funding_reader=PairFundingReader(funding_readers),
            interval=config.live_reconcile_interval,
            max_seconds=config.paper_max_seconds,
            target_capture=config.paper_target_convergence,
            trailing=config.paper_trailing_drawdown,
            on_update=monitor_update,
        )
        await live_monitor.init()
        secondary = await build_bundle(
            config.exchanges,
            config.notional,
            config.paper_entry_edge,
            config.interval,
            strategy_runtime,
            strategy_diary_record,
            config.db_path,
            scanner.funding,
            scanner.universe.symbols if scanner.universe else (),
        )
        sf_cycle = secondary.runtime.services["spot_futures"]
        sf_cycle.allow_open = (
            lambda x: risk.can_open_paper()
            and venue_controller.get(x["exchange"])["scan"]
            and venue_controller.get(x["exchange"])["paper"]
        )
        sf_cycle.service.source.allowed_venue = (
            lambda v: venue_controller.get(v)["scan"]
            and strategy_runtime.enabled["spot_futures"]
        )
        secondary.sf_paper.capital = config.paper_capital
        secondary.sf_paper.reserved = lambda: paper.used_capital
        paper.external_reserved = lambda: secondary.sf_paper.used_capital
        secondary.runtime.services["spot_spot"].source.allowed_venue = (
            lambda v: venue_controller.get(v)["scan"]
        )
        if "funding_arb" in secondary.runtime.services:
            secondary.runtime.services["funding_arb"].service.allowed_venue = (
                lambda v: venue_controller.get(v)["scan"]
            )

        async def sf_closed(position):
            risk.on_paper_close(position.net)
            bankroll.apply(position.net)
            await ledger_add(
                config.db_path,
                datetime.now(timezone.utc).timestamp(),
                "SPOT_FUTURES_PAPER_NET",
                position.net,
                "sf:" + str(position.id),
                position.exchange,
                position.status,
            )
            await notify_paper_close(position.base, position.net, position.status)

        sf_cycle.on_closed = sf_closed
        await secondary.runtime.start()
        bot = Bot(token=config.token)
        notification_bot = bot
        await live_monitor.start()
        task = asyncio.create_task(scanning())
        await dp.start_polling(bot)
    finally:
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if live_monitor:
            await live_monitor.stop_task()
        if secondary:
            await secondary.close()
        await scanner.close()
        await close_clients(private_clients)
        notification_bot = None
        if bot:
            await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
