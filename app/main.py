import time
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
from .risk import RiskGuard
from .private_registry import PrivateRegistry
from .private_order_reader import Reader as PrivateOrderReader
from .private_order_stream import Streams as PrivateOrderStreams, StreamReader
from .private_funding_reader import (
    Reader as PrivateFundingReader,
    PairReader as PairFundingReader,
)
from .live_market_reader import Reader as LiveMarketReader
from .live_monitor import Monitor as LiveMonitor
from .live_exit_dispatch import Coordinator as LiveExitCoordinator
from .live_residual_dispatch import Coordinator as LiveResidualCoordinator
from .live_entry_dispatch import Coordinator as LiveEntryCoordinator
from .live_acceptance import (
    accepted as live_accepted,
    spot_accepted,
    cash_spot_accepted,
)
from .live_funding_dispatch import Coordinator as FundingLiveCoordinator
from .spot_spot_live import Session as SpotSpotSession
from .live_spot_spot_dispatch import Coordinator as SpotSpotCoordinator, CashObserver
from .tg_remaining_live import (
    render as render_remaining_live,
    menu as remaining_live_menu,
)
from .spot_future_live_preflight import Admission as SpotFutureAdmission
from .spot_future_live_session import Session as SpotFutureSession
from .live_spot_future_dispatch import Coordinator as SpotFutureCoordinator
from .spot_executor import SpotOrderReader
from .tg_spot_future_live import render as render_sf_live, menu as sf_live_menu
from .safe_executor import SafeExecutor
from .ccxt_executor import CCXTExecutor
from .recovery_market import Reader as RecoveryMarketReader
from .live_monitor_view import (
    status as monitor_status,
    positions as monitor_positions,
    incidents as monitor_incidents,
)
from .runtime_state import RuntimeTrade
from .private_factory import build_private_readers, build_spot_clients, close_clients
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
from .strategy_toggle_store import Store as StrategyToggleStore
from .venue_mode_store import Store as VenueModeStore
from .venue_controller import Controller as VenueController
from .tg_venue_keyboard import list_keyboard as venue_keyboard, control as venue_control
from .tg_venue_detail import render as venue_detail
from .live_control_view import render as render_live_control
from .live_preflight_view import render as render_live_preflight
from .live_commands import stop as command_stop, resume as command_resume
from .live_commands import clear_monitor_kill
from .resume_evidence import collect_current as collect_resume_current
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
from .tg_ui import main_menu, back_menu, live_menu, replay_menu, strategy_menu
from .spot_future_history_replay import (
    build as build_sf_replay,
    render as render_sf_replay,
)
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
from .spot_spot_view import render as render_spot_inventory
from .funding_paper_view import render as render_funding_paper
from .cex_dex_paper_view import render as render_dex_paper
from .dex_execution_stress import build as build_dex_stress, render as render_dex_stress
from .dex_wallet_observer import render as render_wallet
from .project_readiness import build as build_readiness, render as render_readiness
from .book_history import Store as BookHistory
from .stream_book_recorder import Recorder as StreamBookRecorder
from .execution_book_replay import (
    build as build_execution_replay,
    render as render_execution_replay,
)
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
spot_private_clients = {}
spot_future_live = None
spot_spot_live = None
funding_live = None
live_supervisor = LiveSupervisor(config.max_engine_errors)
live_stop = None
strategy_runtime = StrategyRuntime()
bankroll = Ledger(config.paper_capital)
notification_bot = None
secondary = None
live_monitor = None
live_exit_coordinator = None
live_entry_coordinator = None
live_residual_coordinator = None
live_trades = []
durable = LiveTradeStore(config.db_path)
strategy_toggles = StrategyToggleStore(config.runtime_state_path + ".strategies.json")
strategy_runtime.enabled = strategy_toggles.load(strategy_runtime.enabled)
venue_controller = VenueController(
    VenueModeStore(config.runtime_state_path + ".venues.json"), config.exchanges
)


def menu():
    return main_menu(scanner.paused)


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
        return f"🧪 PAPER\nКапитал: ${paper.budget():.2f}\nАктивных позиций нет."
    lines = [
        f"🧪 PAPER • капитал ${paper.budget():.2f} • занято ${paper.used_capital:.2f}"
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
        if secondary and secondary.dex_paper:
            return render_dex_paper(secondary.dex_paper)
        rows = strategy_runtime.top("cex_dex")
        if not rows:
            return (
                render_dex_console()
                + "\n\nДля price-research нужны ZEROX_API_KEY и DEX_RESEARCH_ROUTES_JSON."
            )
        out = ["⛓ DEX • проверка котировок и исполнения"]
        for row in rows:
            q = row["quote"]
            out.append(
                escape(row["symbol"])
                + " • "
                + escape(
                    str(
                        row.get(
                            "reason",
                            (
                                "buy raw " + q.get("buy_amount_raw", "—")
                                if q["ok"]
                                else q["reason"]
                            ),
                        )
                    )
                )
            )
            if row.get("net_ceiling_model") is not None:
                out.append(
                    f"NET-модель полного схождения: {row['net_ceiling_model']:+.4f} USD • газ {row['gas_usd']:.4f} USD"
                )
            if q.get("simulation_verified"):
                out.append(
                    "Firm quote + RPC симуляция подтверждены. Это модель, не совершённая сделка."
                )
        out.append("Заявки CEX, approvals и транзакции кошелька не отправляются.")
        return "\n".join(out)
    if s == "top":
        return render_market_console(strategy_runtime)
    if s == "paper":
        text = render_positions(paper, secondary.sf_paper if secondary else None)
        if secondary:
            text += render_spot_inventory(secondary.ss_paper, positions_only=True)
            text += render_funding_paper(secondary.funding_paper)
            text += render_dex_paper(secondary.dex_paper)
        return text
    if s == "dex_paper":
        return render_dex_paper(secondary.dex_paper if secondary else None)
    if s == "dex_stress":
        return render_dex_stress(await build_dex_stress(config.db_path))
    if s == "dex_wallet":
        return await render_wallet(config.db_path)
    if s == "readiness":
        return render_readiness(
            await build_readiness(
                config.db_path, config.live_acceptance_path, config.live_exit_venues
            )
        )
    if s == "dex_replay":
        return render_sf_replay(
            await build_sf_replay(
                config.db_path,
                max_gap=max(120, config.interval * 3),
                strategy="cex_dex",
            )
        )
    if s == "funding_paper":
        return render_funding_paper(secondary.funding_paper if secondary else None)
    if s == "fund_replay":
        return render_sf_replay(
            await build_sf_replay(
                config.db_path,
                max_gap=max(120, config.interval * 3),
                strategy="funding_arb",
            )
        )
    if s == "ss_inventory":
        return render_spot_inventory(secondary.ss_paper if secondary else None)
    if s == "ss_replay":
        return render_sf_replay(
            await build_sf_replay(
                config.db_path,
                max_gap=max(120, config.interval * 3),
                strategy="spot_spot",
            )
        )
    if s == "sf_replay":
        return render_sf_replay(
            await build_sf_replay(config.db_path, max_gap=max(120, config.interval * 3))
        )
    if s == "execution_replay":
        return render_execution_replay(await build_execution_replay(config.db_path))
    if s == "replay":
        return render_sf_replay(
            await build_sf_replay(
                config.db_path,
                max_gap=max(120, config.interval * 3),
                strategy="futures_futures",
            )
        )
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
        return f"🛡 RISK CENTER\nСтатус: {'🛑 HALT' if rs.halted else '🟢 NORMAL'}\nПричина: {rs.reason or '—'}\nОшибок подряд: {rs.consecutive_errors}/{risk.max_errors}\nPaper PnL сегодня: {rs.paper_daily_pnl:+.4f} USD\nDaily stop: -{risk.bankroll*risk.daily_stop_pct/100:.2f} USD\nLIVE: текущий STOP и допуск — в LIVE-контроле"
    if s == "startup":
        return startup_text
    if s == "campaign":
        return render_campaign(await campaign_status(diary))
    if s == "live":
        summary = live_monitor.latest if live_monitor else None
        realized = (summary or {}).get("realized", {}).get("net", 0)
        return (
            render_live_control(
                live_supervisor,
                live_trades,
                realized,
                live_stop,
                exit_configured=bool(config.live_enabled and config.live_exit_venues),
                entry_configured=bool(
                    config.live_enabled and config.live_entry_enabled
                ),
                entry_status=(
                    live_entry_coordinator.latest if live_entry_coordinator else None
                ),
            )
            + "\n\n"
            + monitor_status(summary)
        )
    if s == "live_checks":
        result = (
            await live_entry_coordinator.preview(latest[0])
            if live_entry_coordinator and latest
            else None
        )
        return render_live_preflight(result)
    if s in ("sf_live", "sf_checks"):
        preview = None
        offers = strategy_runtime.top("spot_futures", 1)
        if s == "sf_checks":
            preview = (
                await spot_future_live.session.admission.preview(offers[0])
                if spot_future_live and offers
                else dict(status="NO_OPPORTUNITY")
            )
        return render_sf_live(
            live_monitor.latest if live_monitor else None,
            spot_future_live,
            bool(
                config.live_enabled
                and config.live_entry_enabled
                and config.live_spot_futures_enabled
            ),
            preview,
        )
    if s in ("ss_live", "ss_checks", "funding_live", "funding_checks"):
        strategy = "spot_spot" if s.startswith("ss_") else "funding_arb"
        coordinator = spot_spot_live if strategy == "spot_spot" else funding_live
        preview = None
        offers = strategy_runtime.top(strategy, 1)
        if s.endswith("checks"):
            if coordinator and offers:
                offer = offers[0]
                if strategy == "funding_arb":
                    preview = await coordinator.preview(
                        dict(
                            symbol=offer["symbol"],
                            buy=offer["long_venue"],
                            sell=offer["short_venue"],
                        )
                    )
                else:
                    preview = await coordinator.session.preview(offer)
            else:
                preview = dict(status="NO_OPPORTUNITY", orders_sent=False)
        return render_remaining_live(
            strategy,
            live_monitor.latest if live_monitor else None,
            coordinator,
            preview,
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
        live_label = (
            "настроен • допуск и STOP смотрите в LIVE-контроле"
            if config.live_enabled
            else "не настроен"
        )
        return f"📡 СТАТУС\nUniverse: {coverage} рынков\nСканер: {'⏸' if scanner.paused else '🟢'}\nБирж: {len(scanner.clients)}\nПоследний цикл: {stamp}\nPaper: {len(paper.positions)}/{paper.max_positions}\nRisk: {'🛑 '+rs.reason if rs.halted else '🟢 OK'} • day {rs.paper_daily_pnl:+.4f}$\nLIVE: {live_label}"
    return "Неизвестный раздел"


@dp.message(CommandStart())
async def start(m: Message):
    if allowed(m.from_user.id):
        await m.answer(
            await text_for("home"),
            reply_markup=menu(),
            parse_mode="HTML",
        )


@dp.message(
    Command(
        "top",
        "paper",
        "diary",
        "replay",
        "sf_replay",
        "ss_replay",
        "fund_replay",
        "dex_replay",
        "dex_stress",
        "dex_wallet",
        "readiness",
        "execution_replay",
        "funding_paper",
        "dex_paper",
        "ss_inventory",
        "exchanges",
        "status",
        "risk",
        "startup",
        "campaign",
        "live",
        "live_stop",
        "live_resume",
        "live_clear",
        "live_checks",
        "sf_live",
        "sf_checks",
        "ss_live",
        "ss_checks",
        "funding_live",
        "funding_checks",
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
    notice = ""
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
        ev = collect_resume_current(
            live_supervisor, live_monitor.latest if live_monitor else None, time.time()
        )
        result = command_resume(live_stop, ev, live_supervisor.kill)
        notice = (
            "▶ STOP снят."
            if result.allowed
            else "🛑 STOP остаётся: " + escape(result.reason)
        )
        s = "live"
    if s == "live_clear":
        async with live_monitor.lock:
            result = clear_monitor_kill(
                live_supervisor, live_monitor.latest, time.time()
            )
        notice = (
            "Блокировка снята. STOP остаётся; снимите его отдельно."
            if result.allowed
            else "Блокировка остаётся: " + escape(result.reason)
        )
        s = "live"
    await m.answer(
        (notice + "\n\n" if notice else "") + await text_for(s),
        reply_markup=keyboard_for(s),
        parse_mode="HTML",
    )


@dp.callback_query(
    F.data.in_(
        {
            "home",
            "top",
            "paper",
            "diary",
            "replay",
            "sf_replay",
            "ss_replay",
            "fund_replay",
            "dex_replay",
            "dex_stress",
            "dex_wallet",
            "readiness",
            "execution_replay",
            "funding_paper",
            "dex_paper",
            "ss_inventory",
            "exchanges",
            "status",
            "risk",
            "startup",
            "campaign",
            "live",
            "live_stop",
            "live_resume",
            "live_clear",
            "live_checks",
            "sf_live",
            "sf_checks",
            "ss_live",
            "ss_checks",
            "funding_live",
            "funding_checks",
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
    notice = ""
    s = q.data
    if s == "export":
        await q.answer("Готовлю выгрузку")
        await send_export(q.message)
        return
    await q.answer()
    if s in ("pause", "resume"):
        scanner.paused = s == "pause"
        s = "status"
    if s == "live_stop":
        command_stop(live_stop)
        s = "live"
    if s == "live_resume":
        ev = collect_resume_current(
            live_supervisor, live_monitor.latest if live_monitor else None, time.time()
        )
        result = command_resume(live_stop, ev, live_supervisor.kill)
        notice = (
            "▶ STOP снят."
            if result.allowed
            else "🛑 STOP остаётся: " + escape(result.reason)
        )
        s = "live"
    if s == "live_clear":
        async with live_monitor.lock:
            result = clear_monitor_kill(
                live_supervisor, live_monitor.latest, time.time()
            )
        notice = (
            "Блокировка снята. STOP остаётся; снимите его отдельно."
            if result.allowed
            else "Блокировка остаётся: " + escape(result.reason)
        )
        s = "live"
    await safe_edit(
        q.message,
        (notice + "\n\n" if notice else "") + await text_for(s),
        keyboard_for(s),
    )


@dp.callback_query(F.data.startswith("sf_close:") | F.data.startswith("sf_recover:"))
async def spot_live_action(q: CallbackQuery):
    if not allowed(q.from_user.id):
        await q.answer("Нет доступа", show_alert=True)
        return
    await q.answer("Проверяю исполнение")
    if not spot_future_live or not live_monitor:
        return
    action, tid = q.data.split(":", 1)
    async with live_monitor.lock:
        if action == "sf_close":
            result = await spot_future_live.session.close(tid)
        else:
            result = await spot_future_live.session.recover(tid)
    await live_monitor.cycle()
    await safe_edit(
        q.message,
        "Результат действия: <code>"
        + escape(str(result.get("status", "UNKNOWN")))
        + "</code>\n\n"
        + await text_for("sf_live"),
        keyboard_for("sf_live"),
    )


@dp.callback_query(F.data.startswith("ss_close:") | F.data.startswith("ss_recover:"))
async def spot_spot_action(q: CallbackQuery):
    if not allowed(q.from_user.id):
        await q.answer("Нет доступа", show_alert=True)
        return
    await q.answer("Проверяю остатки")
    if not spot_spot_live or not live_monitor:
        return
    action, tid = q.data.split(":", 1)
    async with live_monitor.lock:
        result = await (
            spot_spot_live.session.close(tid)
            if action == "ss_close"
            else spot_spot_live.session.recover(tid)
        )
    await live_monitor.cycle()
    await safe_edit(
        q.message,
        "Результат: <code>"
        + escape(str(result.get("status", "UNKNOWN")))
        + "</code>\n\n"
        + await text_for("ss_live"),
        keyboard_for("ss_live"),
    )


@dp.callback_query(F.data.startswith("fund_close:"))
async def funding_close_action(q: CallbackQuery):
    if not allowed(q.from_user.id):
        await q.answer("Нет доступа", show_alert=True)
        return
    await q.answer("Проверяю выход")
    if not live_monitor or not live_exit_coordinator:
        return
    tid = q.data.split(":", 1)[1]
    summary = await live_monitor.cycle()
    from copy import deepcopy

    requested = deepcopy(summary)
    requested["trades"] = [
        x
        for x in requested["trades"]
        if x["trade_id"] == tid and x.get("strategy") == "funding_arb"
    ]
    for x in requested["trades"]:
        x["exit_signal"] = "OPERATOR_EXIT"
    async with live_monitor.lock:
        outcomes = await live_exit_coordinator.process(requested)
    await live_monitor.cycle()
    notice = (
        "; ".join(x.status for x in outcomes)
        if outcomes
        else "Нет подтверждённой активной позиции"
    )
    await safe_edit(
        q.message,
        "Выход: <code>"
        + escape(notice)
        + "</code>\n\n"
        + await text_for("funding_live"),
        keyboard_for("funding_live"),
    )


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
        return live_menu(live_stop.stopped, bool(live_supervisor.kill.global_reason))
    if screen in ("ss_live", "ss_checks", "funding_live", "funding_checks"):
        return remaining_live_menu(
            "spot_spot" if screen.startswith("ss_") else "funding_arb",
            live_monitor.latest,
        )
    if screen in ("sf_live", "sf_checks"):
        return sf_live_menu(live_monitor.latest if live_monitor else None)
    if screen == "top":
        return market_keyboard(merged_market(strategy_runtime))
    if screen == "exchanges":
        return venue_keyboard(scanner.ids)
    if screen == "strategies":
        return strategy_menu(strategy_runtime.enabled)
    if screen in (
        "replay",
        "sf_replay",
        "ss_replay",
        "fund_replay",
        "dex_replay",
        "execution_replay",
    ):
        return replay_menu(screen)
    if screen == "paper":
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⛓ CEX/DEX Paper", callback_data="dex_paper"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🕒 Funding Paper", callback_data="funding_paper"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🏦 Запасы Spot/Spot", callback_data="ss_inventory"
                    )
                ],
                [
                    InlineKeyboardButton(text="↻ Обновить", callback_data="paper"),
                    InlineKeyboardButton(text="‹ Главное меню", callback_data="home"),
                ],
            ]
        )
    if screen in ("dex", "dex_paper", "dex_wallet", "dex_stress"):
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⛓ Paper",
                        callback_data="dex_paper",
                        style="primary" if screen == "dex_paper" else None,
                    ),
                    InlineKeyboardButton(text="🧪 Replay", callback_data="dex_replay"),
                ],
                [
                    InlineKeyboardButton(
                        text="⏱ Задержки / fills",
                        callback_data="dex_stress",
                        style="primary" if screen == "dex_stress" else None,
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Кошелёк · receipts",
                        callback_data="dex_wallet",
                        style="primary" if screen == "dex_wallet" else None,
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🏁 Готовность проекта", callback_data="readiness"
                    )
                ],
                [
                    InlineKeyboardButton(text="↻ Обновить", callback_data=screen),
                    InlineKeyboardButton(text="‹ Главное меню", callback_data="home"),
                ],
            ]
        )
    if screen == "funding_paper":
        return back_menu(screen, "paper")
    if screen == "ss_inventory":
        return back_menu(screen, "paper")
    if screen in ("live_positions", "incidents", "live_checks"):
        return back_menu(screen, "live")
    return back_menu(screen)


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
    await safe_edit(q.message, render_market_detail(name, row), back_menu(parent="top"))
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
    if "native_plan" in op and op["native_plan"].get("valid") is not True:
        return "NATIVE_PLAN_BLOCKED:" + op["native_plan"].get("reason", "UNKNOWN")
    if scanner.paused:
        return "SCANNER_PAUSED"
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
            scanner.watch_positions = {
                (p.symbol, p.buy, p.sell): p for p in paper.positions.values()
            }
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
                (
                    latest
                    if strategy_runtime.enabled["futures_futures"]
                    and not scanner.paused
                    else []
                ),
            )
            await strategy_diary_record(
                config.db_path,
                [strategy_row({**x, "strategy": "futures_futures"}) for x in latest],
            )
            risk.on_success()
            await diary.record(quotes)
            if (
                live_entry_coordinator
                and config.live_entry_enabled
                and not scanner.paused
                and strategy_runtime.enabled["futures_futures"]
            ):
                async with live_monitor.lock:
                    await live_entry_coordinator.process(latest)
                live_monitor.request_cycle()
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
                reason = (
                    "CLOSED_THIS_CYCLE"
                    if o["symbol"] in {p.symbol for p in closed}
                    else paper_entry_reason(o)
                )
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
    global spot_spot_live, funding_live
    global startup_text, private_clients, secondary, live_trades, live_stop, notification_bot, live_monitor, live_exit_coordinator, live_entry_coordinator, live_residual_coordinator
    global spot_private_clients, spot_future_live
    live_stop = Stop(config.runtime_state_path + ".stop.json")
    pf = preflight_check(config)
    if not pf.ok:
        raise RuntimeError(render_preflight(pf))
    if pf.warnings:
        log.warning("%s", render_preflight(pf).replace("\\n", " | "))
    secondary = None
    live_monitor = None
    live_exit_coordinator = None
    live_entry_coordinator = None
    live_residual_coordinator = None
    private_clients = {}
    spot_private_clients = {}
    spot_future_live = None
    spot_spot_live = None
    funding_live = None
    bot = None
    task = None
    stream_recorder = None
    order_streams = None
    try:
        Path(config.db_path).parent.mkdir(parents=True, exist_ok=True)
        await diary.init()
        scanner.on_books = None
        if config.record_books:
            book_history = BookHistory(
                config.db_path,
                max_rows=config.book_history_max_rows,
                retention_seconds=config.book_history_hours * 3600,
            )
            await book_history.init()
            scanner.on_books = book_history.record
        await strategy_diary_init(config.db_path)
        await ledger_init(config.db_path)
        await durable.init()
        await paper.restore()
        await restore_ledger(config.db_path, bankroll, risk)
        readers, private_clients = build_private_readers()
        spot_private_clients = build_spot_clients()

        async def load_spot(venue, client):
            try:
                await asyncio.wait_for(client.load_markets(), 10)
            except Exception:
                log.warning("Spot private metadata unavailable: %s", venue)

        await asyncio.gather(
            *(load_spot(v, c) for v, c in spot_private_clients.items())
        )
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
        if config.record_books:
            stream_recorder = StreamBookRecorder(
                book_history, scanner.specs, config.public_stream_record_interval
            )
            scanner.book_recorder = stream_recorder
            for venue, client in scanner.clients.items():
                if hasattr(client, "book_status"):
                    client.on_book = lambda book, venue=venue: stream_recorder.offer(
                        venue, book
                    )
            stream_recorder.start()
        registry = PrivateRegistry()
        for name, reader in readers.items():
            registry.add(name, reader)
        order_readers = {
            name: PrivateOrderReader(name, client)
            for name, client in private_clients.items()
        }
        order_readers.update(
            {
                v + ":spot": SpotOrderReader(v + ":spot", c)
                for v, c in spot_private_clients.items()
            }
        )
        funding_readers = {
            name: PrivateFundingReader(name, client)
            for name, client in private_clients.items()
        }

        def exit_authority(trade):
            return bool(
                config.live_enabled
                and not live_stop.stopped
                and all(
                    v in config.live_exit_venues
                    for v in (trade.long_venue, trade.short_venue)
                )
                and not live_supervisor.kill.check(
                    trade.symbol, trade.long_venue, trade.short_venue
                ).blocked
            )

        def venue_exit_authority(venue):
            # SafeExecutor checks again immediately before sending each leg.
            return bool(
                config.live_enabled
                and venue in config.live_exit_venues
                and not live_stop.stopped
                and not live_supervisor.kill.pairs
                and not live_supervisor.kill.check("", venue, venue).blocked
            )

        def spot_entry_authority(venue, strategy="spot_futures"):
            summary = live_monitor.latest if live_monitor else {}
            return bool(
                config.live_enabled
                and config.live_entry_enabled
                and (
                    config.live_spot_futures_enabled
                    if strategy == "spot_futures"
                    else config.live_spot_spot_enabled
                )
                and strategy_runtime.enabled.get(strategy, False)
                and not scanner.paused
                and not live_stop.stopped
                and venue in config.live_exit_venues
                and venue_controller.get(venue)["scan"]
                and config.live_no_withdraw_attested
                and not live_supervisor.kill.pairs
                and not live_supervisor.kill.check("", venue, venue).blocked
                and summary
                and summary.get("private_verified") is True
                and summary.get("reconciled") is True
                and summary.get("unknown_orders") == 0
                and 0 <= time.time() - summary.get("ts", 0) <= 15
                and (
                    strategy == "spot_spot"
                    or (
                        order_streams is not None
                        and venue in order_streams.tasks
                        and not order_streams.tasks[venue].done()
                        and venue in order_streams.observed
                        and venue not in order_streams.errors
                    )
                )
                and (
                    spot_accepted(config.live_acceptance_path, venue)
                    if strategy == "spot_futures"
                    else cash_spot_accepted(config.live_acceptance_path, venue)
                )
            )

        spot_admission = SpotFutureAdmission(
            spot_private_clients,
            private_clients,
            scanner.funding,
            bankroll=config.live_capital,
            notional=config.notional,
            minimum_net=config.live_min_net_edge_usd,
            safety_pct=config.safety_buffer_pct,
            hold_seconds=config.paper_max_seconds,
        )
        spot_future_live = SpotFutureCoordinator(
            SpotFutureSession(
                durable,
                diary,
                spot_admission,
                spot_entry_authority,
                venue_exit_authority,
                halt=live_stop.stop,
            ),
            max_seconds=config.paper_max_seconds,
            target=config.paper_target_convergence,
            trailing=config.paper_trailing_drawdown,
        )

        spot_spot_live = SpotSpotCoordinator(
            SpotSpotSession(
                durable,
                diary,
                spot_private_clients,
                lambda v: spot_entry_authority(v, "spot_spot"),
                venue_exit_authority,
                halt=live_stop.stop,
                bankroll=config.live_capital,
                notional=config.notional,
                minimum_net=config.live_min_net_edge_usd,
                safety_pct=config.safety_buffer_pct,
            ),
            max_seconds=config.paper_max_seconds,
            target=config.paper_target_convergence,
            trailing=config.paper_trailing_drawdown,
        )

        exit_executors = {
            venue: SafeExecutor(
                venue,
                CCXTExecutor(venue, client),
                diary,
                lambda: False,
                exit_gate=lambda venue=venue: venue_exit_authority(venue),
            )
            for venue, client in private_clients.items()
        }
        live_exit_coordinator = LiveExitCoordinator(
            durable,
            exit_executors,
            RecoveryMarketReader(scanner.clients),
            exit_authority,
            live_stop,
            max_age=config.live_max_book_age_ms / 1000,
            residual_recovery=True,
        )
        live_residual_coordinator = LiveResidualCoordinator(
            durable,
            diary,
            exit_executors,
            registry.snapshot,
            RecoveryMarketReader(scanner.clients),
            exit_authority,
            live_stop,
        )

        async def monitor_update(summary, new_incidents):
            global live_trades
            live_trades = [RuntimeTrade(**row) for row in summary["runtime_trades"]]
            await live_exit_coordinator.process(summary)
            await live_residual_coordinator.process(summary)
            summary["closed"].extend(await spot_future_live.process(summary))
            summary["closed"].extend(await spot_spot_live.process(summary))
            summary["realized"] = await live_monitor.store.totals()
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
                            f"✅ Итог подтверждён private API\n{escape(result['trade_id'])}\nФактический NET: {result['net']:+.4f} USD"
                            + (
                                f"\nОстался актив: {result['held_inventory_base']:.8g} BASE • учтён отдельно, не flat."
                                if result.get("held_inventory_base")
                                else ""
                            ),
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
            funding_reader=PairFundingReader(funding_readers, calendar=scanner.funding),
            interval=config.live_reconcile_interval,
            max_seconds=config.paper_max_seconds,
            target_capture=config.paper_target_convergence,
            trailing=config.paper_trailing_drawdown,
            on_update=monitor_update,
            cash_observer=CashObserver(spot_future_live, spot_spot_live),
            stop_net=-config.live_capital * 0.01,
        )
        await live_monitor.init()
        if config.private_order_streams:
            order_streams = PrivateOrderStreams(
                private_clients, config.db_path, live_monitor.request_cycle
            )
            await order_streams.init()
            for venue, client in private_clients.items():
                order_readers[venue] = StreamReader(venue, client, order_streams)
            order_streams.start()

        def entry_authority(
            symbol, long_venue, short_venue, strategy="futures_futures"
        ):
            venues = (long_venue, short_venue)
            return bool(
                config.live_enabled
                and config.live_entry_enabled
                and strategy_runtime.enabled.get(strategy, False)
                and not scanner.paused
                and not live_stop.stopped
                and all(
                    v in config.live_exit_venues and venue_controller.get(v)["scan"]
                    for v in venues
                )
                and config.live_no_withdraw_attested
                and not live_supervisor.kill.check(
                    symbol, long_venue, short_venue
                ).blocked
                and order_streams is not None
                and all(
                    v in order_streams.tasks
                    and not order_streams.tasks[v].done()
                    and v in order_streams.observed
                    and v not in order_streams.errors
                    for v in venues
                )
                and live_accepted(config.live_acceptance_path, venues)
            )

        live_entry_coordinator = LiveEntryCoordinator(
            durable,
            runtime_store,
            diary,
            scanner.clients,
            private_clients,
            registry.snapshot,
            scanner.funding,
            entry_authority,
            exit_authority=lambda symbol, lv, sv: exit_authority(
                SimpleNamespace(symbol=symbol, long_venue=lv, short_venue=sv)
            ),
            bankroll=config.live_capital,
            notional=config.notional,
            minimum_net=config.live_min_net_edge_usd,
            safety_pct=config.safety_buffer_pct,
            max_seconds=config.paper_max_seconds,
        )
        live_entry_coordinator.halt = live_stop.stop
        funding_live = FundingLiveCoordinator(
            durable,
            runtime_store,
            diary,
            scanner.clients,
            private_clients,
            registry.snapshot,
            scanner.funding,
            lambda symbol, lv, sv: config.live_funding_enabled
            and entry_authority(symbol, lv, sv, "funding_arb")
            and live_accepted(
                config.live_acceptance_path, (lv, sv), strategy="funding_arb"
            ),
            exit_authority=lambda symbol, lv, sv: exit_authority(
                SimpleNamespace(symbol=symbol, long_venue=lv, short_venue=sv)
            ),
            bankroll=config.live_capital,
            notional=config.notional,
            minimum_net=config.live_min_net_edge_usd,
            safety_pct=config.safety_buffer_pct,
            max_seconds=config.live_funding_hold_seconds,
            min_carry_pct=config.live_funding_min_carry_pct,
        )
        funding_live.halt = live_stop.stop
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
            private_clients=private_clients,
        )
        secondary.runtime.paused = lambda: scanner.paused

        async def secondary_rows(name, rows, entry_enabled):
            coordinator = {
                "spot_futures": spot_future_live,
                "spot_spot": spot_spot_live,
                "funding_arb": funding_live,
            }.get(name)
            if entry_enabled and coordinator:
                async with live_monitor.lock:
                    await coordinator.process_rows(rows)
                live_monitor.request_cycle()

        secondary.runtime.on_rows = secondary_rows
        sf_cycle = secondary.runtime.services["spot_futures"]
        sf_cycle.allow_open = (
            lambda x: not scanner.paused
            and risk.can_open_paper()
            and venue_controller.get(x["exchange"])["scan"]
            and venue_controller.get(x["exchange"])["paper"]
        )
        sf_cycle.service.source.allowed_venue = (
            lambda v: not scanner.paused
            and venue_controller.get(v)["scan"]
            and strategy_runtime.enabled["spot_futures"]
        )
        secondary.sf_paper.capital = config.paper_capital
        ss = secondary.ss_paper
        ss.capital = config.paper_capital
        ss.max_age = config.paper_max_seconds
        ss.trailing = config.paper_trailing_drawdown
        fp = secondary.funding_paper
        dpaper = getattr(secondary, "dex_paper", None)
        for module in (paper, secondary.sf_paper, ss, fp, dpaper):
            if module is not None:
                module.budget = lambda: bankroll.equity
        funding_reserved = lambda: fp.used_capital if fp else 0
        dex_reserved = lambda: dpaper.used_capital if dpaper else 0
        ss.external_reserved = (
            lambda: paper.used_capital
            + secondary.sf_paper.used_capital
            + funding_reserved()
            + dex_reserved()
        )
        if (
            getattr(ss, "allocated_capital", ss.used_capital) + ss.external_reserved()
            > config.paper_capital
        ):
            raise ValueError("SHARED_PAPER_INVENTORY_BUDGET_EXCEEDED")
        ss.allow_open = (
            lambda x: not scanner.paused
            and risk.can_open_paper()
            and all(
                venue_controller.get(v)["paper"] and venue_controller.get(v)["scan"]
                for v in (x["buy"], x["sell"])
            )
        )
        secondary.sf_paper.reserved = (
            lambda: paper.used_capital
            + ss.used_capital
            + funding_reserved()
            + dex_reserved()
        )
        paper.external_reserved = (
            lambda: secondary.sf_paper.used_capital
            + ss.used_capital
            + funding_reserved()
            + dex_reserved()
        )
        secondary.runtime.services["spot_spot"].source.allowed_venue = (
            lambda v: not scanner.paused and venue_controller.get(v)["scan"]
        )
        if "funding_arb" in secondary.runtime.services:
            secondary.runtime.services["funding_arb"].service.allowed_venue = (
                lambda v: not scanner.paused and venue_controller.get(v)["scan"]
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

        async def ss_closed(position):
            risk.on_paper_close(position["net"])
            bankroll.apply(position["net"])
            await ledger_add(
                config.db_path,
                position["closed_at"],
                "SPOT_SPOT_PAPER_NET",
                position["net"],
                "ss:" + str(position["id"]),
                note=position["symbol"],
            )
            await notify_paper_close(
                position["symbol"], position["net"], position["status"]
            )

        ss.on_closed = ss_closed
        if fp:
            fp.external_reserved = (
                lambda: paper.used_capital
                + secondary.sf_paper.used_capital
                + ss.used_capital
                + dex_reserved()
            )
            fp.allow_open = ss.allow_open

            async def funding_closed(position):
                risk.on_paper_close(position["net"])
                bankroll.apply(position["net"])
                await ledger_add(
                    config.db_path,
                    position["closed_at"],
                    "FUNDING_PAPER_NET",
                    position["net"],
                    "fund:" + str(position["id"]),
                    note=position["symbol"],
                )
                await notify_paper_close(
                    position["symbol"],
                    position["net"],
                    "Funding Paper: история ставок и модель reference notional",
                )

            fp.on_closed = funding_closed
        if dpaper:
            dpaper.external_reserved = (
                lambda: paper.used_capital
                + secondary.sf_paper.used_capital
                + ss.used_capital
                + funding_reserved()
            )
            dpaper.allow_open = (
                lambda p: not scanner.paused
                and risk.can_open_paper()
                and all(
                    venue_controller.get(p["cex_venue"])[k] for k in ("paper", "scan")
                )
            )

            async def dex_closed(position):
                risk.on_paper_close(position["net"])
                bankroll.apply(position["net"])
                await notify_paper_close(
                    position["symbol"],
                    position["net"],
                    "CEX/DEX Paper: firm quote, газ и история funding",
                )

            dpaper.on_closed = dex_closed
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
        if order_streams:
            await order_streams.close()
        if secondary:
            await secondary.close()
        await scanner.close()
        if stream_recorder is not None:
            await stream_recorder.close()
        await close_clients(private_clients)
        await close_clients(spot_private_clients)
        notification_bot = None
        if bot:
            await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
