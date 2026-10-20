import time
import os
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
    market_fallback_accepted,
)
from .live_funding_dispatch import Coordinator as FundingLiveCoordinator
from .live_execution_costs import build as build_live_costs, render as render_live_costs
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
from .walk_forward import build as build_walk_forward, render as render_walk_forward
from .book_priority_runtime import live as live_book_priorities
from .book_history import Store as BookHistory
from .stream_book_recorder import Recorder as StreamBookRecorder
from .execution_book_replay import (
    build as build_execution_replay,
    render as render_execution_replay,
)
from .cash_execution_replay import (
    build as build_cash_execution,
    render as render_cash_execution,
)
from .market_console import merged as merged_market
from .funding_execution_replay import (
    build as build_funding_execution,
    render as render_funding_execution,
)
from .tg_market_keyboard import build as market_keyboard, selection_key
from .tg_market_detail import render as render_market_detail

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("arbitrage")


def bootstrap_checkpoint(stage):
    # Railway can terminate an OOM process without emitting a Python traceback.
    # Leave a bounded marker before each market-heavy startup stage.
    rss = 'unavailable'
    try:
        with open('/proc/self/status', encoding='ascii') as status:
            for line in status:
                if line.startswith('VmRSS:'):
                    rss = f'{int(line.split()[1]) / 1024:.0f} MiB'
                    break
    except OSError:
        pass
    log.info('PAPER BOOTSTRAP %s | RSS %s', stage, rss)

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
from .trading_controls import Controls
trading_controls = Controls(config.runtime_state_path + '.trading_controls.json',
                            paper_notional=config.notional, live_notional=min(5, config.notional))
from .strategy_explanation import describe as describe_strategy
from .strategy_lab import Lab as StrategyLab
strategy_lab = StrategyLab(config.db_path)
strategy_lab_runtime = None
notification_bot = None
secondary = None
from .wallet_watch import Store as WalletWatchStore
wallet_watch_store = WalletWatchStore(config.runtime_state_path + '.wallet_watch.json')
wallet_watch_pending = {}
numeric_pending = {}


@dp.callback_query.middleware()
async def clear_numeric_prompt(handler, event, data):
    # Any navigation invalidates the previous input, including Back and modes.
    numeric_pending.pop(event.from_user.id, None)
    return await handler(event, data)


async def numeric_prompt(q, kind, key):
    from .trading_controls import LIMITS
    from .trading_controls_view import LABELS
    from .transfer_console import FIELDS
    transfer_limits={'budget':(5,100),'min_spread':(2,20),'max_spread':(2,30),
                     'min_net':(.25,5),'stress_drop':(1,10),'dex_deposit_fee_pct':(0,1),'mexc_deposit_fee_pct':(0,1),'daily_loss':(.1,1.25),'total_loss':(.1,2.5)}
    limits=transfer_limits if kind=='sig' else LIMITS
    if key not in limits:raise ValueError('Неизвестный параметр')
    low,high=limits[key];label=FIELDS[key][0] if kind=='sig' else LABELS[key]
    numeric_pending[q.from_user.id]=dict(kind=kind,key=key,chat=q.message.chat.id,
        message=q.message.message_id,expires=time.time()+120,
        environment=trading_controls.environment,revision=trading_controls.state['revision'])
    await q.answer()
    await safe_edit(q.message,f'⚙️ <b>{label}</b>\n\nПришли число от {low:g} до {high:g}.',
        InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='‹ Назад',callback_data=f'{kind}:field:{key}')]]))


@dp.message(lambda m: bool(m.from_user and m.from_user.id in numeric_pending))
async def numeric_setting_message(m: Message):
    if not allowed(m.from_user.id):return
    pending=numeric_pending.get(m.from_user.id)
    if not pending or pending['chat']!=m.chat.id:return
    kind,key=pending['kind'],pending['key']
    if m.text and m.text.startswith('/'):
        numeric_pending.pop(m.from_user.id,None)
        from aiogram.dispatcher.event.bases import SkipHandler
        raise SkipHandler
    back=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='‹ Назад',callback_data=f'{kind}:field:{key}')]])
    try:
        if time.time()>pending['expires']:
            numeric_pending.pop(m.from_user.id,None)
            raise ValueError('Время ввода истекло. Открой параметр заново')
        from .numeric_settings import parse
        value=parse(m.text,integer=key in ('max_positions','slippage_bps'))
        if kind=='sig':
            feed=getattr(secondary,'market_signals',None)
            if feed is None:raise ValueError('Модуль недоступен')
            await feed.store.set_control(key,value)
            from .transfer_console import render
            text,keyboard=await render(feed,'sig:field:'+key,scanner.paused)
        else:
            if (trading_controls.environment,trading_controls.state['revision'])!=(pending['environment'],pending['revision']):
                numeric_pending.pop(m.from_user.id,None)
                raise ValueError('Настройки изменились. Открой параметр заново')
            from .trading_controls import LIMITS
            low,high=LIMITS[key]
            if not low<=value<=high:raise ValueError(f'Допустимо от {low:g} до {high:g}')
            trading_controls.update(**{key:value})
            apply_operator_paper_limits()
            while trading_controls.events:
                event=trading_controls.events[0]
                await diary.record_decisions([dict(ts=event['ts'],strategy='operator_control',symbol='*',
                    buy='',sell='',action='CONFIG_CHANGE',reason='OPERATOR_UPDATE',before=event['before'],after=event['after'])])
                trading_controls.events.pop(0)
            from .trading_controls_view import render
            text,keyboard=render(trading_controls,config.live_enabled and config.live_entry_enabled,selected_key=key)
        numeric_pending.pop(m.from_user.id,None)
    except ValueError as exc:
        text='⚙️ <b>Значение не сохранено</b>\n\n'+escape(str(exc)[:180])
        keyboard=back
    await m.bot.edit_message_text(text,chat_id=pending['chat'],message_id=pending['message'],reply_markup=keyboard,parse_mode='HTML')
    try:await m.delete()
    except Exception:pass

live_monitor = None
live_exit_coordinator = None
live_entry_coordinator = None
live_residual_coordinator = None
live_trades = []
durable = LiveTradeStore(config.db_path)
strategy_toggles = StrategyToggleStore(config.runtime_state_path + ".strategies.json")
strategy_runtime.enabled = strategy_toggles.load(strategy_runtime.enabled)
from .dex_signal_focus import enabled as focus_enabled, focus_toggles, Signals
dual_paper = focus_enabled("DEX_DUAL_PAPER_ENABLED")
paired_paper = config.paired_paper_enabled
paper.paired_mode = paired_paper
if paired_paper:
    from .paired_paper_profile import load_pause
    scanner.paused = load_pause(config.runtime_state_path)
paired_lock = asyncio.Lock()
if paired_paper and not dual_paper:
    raise ValueError('PAIRED_EXPERIMENT_REQUIRES_DUAL_MONITORING')
if not dual_paper and focus_enabled("DEX_CHANNEL_SIGNALS_ONLY") and not focus_enabled("DEX_REFERENCE_FOCUS"):
    raise ValueError("DEX_CHANNEL_SIGNALS_ONLY_REQUIRES_REFERENCE_FOCUS")
if (dual_paper or focus_enabled("DEX_REFERENCE_FOCUS")) and (config.live_enabled or not focus_enabled("DEX_VIRTUAL_PAPER_ENABLED")):
    raise ValueError("DEX_REFERENCE_FOCUS_REQUIRES_VIRTUAL_PAPER_ONLY")
if dual_paper:
    strategy_runtime.enabled.setdefault("dex_channel", False)
    strategy_runtime.enabled.setdefault("dex_bybit", False)
elif focus_enabled("DEX_REFERENCE_FOCUS"):
    strategy_runtime.enabled = focus_toggles(strategy_runtime.enabled)
if not dual_paper:
    strategy_runtime.enabled.pop("dex_channel", None)
    strategy_runtime.enabled.pop("dex_bybit", None)
venue_controller = VenueController(
    VenueModeStore(config.runtime_state_path + ".venues.json"), config.exchanges
)


def menu():
    if paired_paper:
        from .paper_console import main_menu as paper_menu
        return paper_menu(scanner.paused)
    else:
        keyboard = main_menu(scanner.paused)
    return InlineKeyboardMarkup(inline_keyboard=keyboard.inline_keyboard + [
        [InlineKeyboardButton(text='🎛 Среда · режим · риск', callback_data='ctl:show')]])


def operator_paper_reason(strategy, row):
    if paired_paper and getattr(secondary,'inventory_paper',None) and strategy == 'dex_paired':
        if not strategy_runtime.enabled.get('product_dex_paired',False):
            return 'PAIRED_PRODUCT_OPT_IN_REQUIRED'
        if row.get('cex_venue') not in ('bybit','mexc'):
            return 'PAIRED_PRODUCT_VENUE_DISABLED'
        if row.get('fee_source') != 'ACCOUNT_READ_ONLY' and not (
            row.get('cex_venue')=='mexc' and row.get('fee_source')=='MEXC_API_FUTURES_PUBLIC_SCHEDULE_PAPER'
        ):
            return 'PAIRED_PRODUCT_ACCOUNT_FEE_REQUIRED'
    if paired_paper and getattr(secondary,'inventory_paper',None) and strategy != 'dex_paired':
        return 'RESEARCH_ONLY_USE_STRATEGY_LAB'
    risk.roll_day()
    symbol = row.get('symbol') or row.get('base')
    reason = trading_controls.entry_reason('paper', strategy, symbol)
    if reason:
        return reason
    p = row.get('paired_policy') or {}
    notional = row.get('notional', row.get('entry_cash', 0))
    if row.get('entry_price') and row.get('base_qty'):
        notional = max(notional, row['base_qty'] * row['entry_price'])
    positions = len(paper.positions)
    dex = getattr(secondary, 'dex_paired', None)
    positions += len(dex.positions) if dex else 0
    if secondary:
        for name in ('sf_paper', 'ss_paper', 'funding_paper'):
            module = getattr(secondary, name, None)
            positions += len(getattr(module, 'positions', {}))
    day_net = risk.state.paper_daily_pnl
    if dex and dex.state.get('day') == int(time.time()//86400):
        day_net += dex.state.get('day_net', 0)
    projected = p.get('partial_net')
    if p and strategy == 'dex_paired':
        row['paired_policy'] = trading_controls.tighten_policy(p)
        if -row.get('entry_immediate_net', 0) >= row['paired_policy']['stop_usd']:
            return 'OPERATOR_STOP_TOO_TIGHT'
    return trading_controls.risk_reason(notional, positions, day_net, projected,
                                       row.get('executable'))


def apply_operator_paper_limits():
    p = trading_controls.profile('paper')
    paper.max_positions = p['max_positions']
    # A smaller limit halts new entries; it never liquidates or rewrites the
    # exit policy of an existing position.
    risk.daily_stop_pct = min(config.daily_stop_pct, p['daily_loss_usd']/risk.bankroll*100)


async def paper_futures_entry(row):
    from .paired_futures_quotes import refresh
    from .paired_paper_profile import futures_cooldown
    # Always re-fetch executable prices, including manual confirmations.
    fresh = await refresh(row, scanner)
    reason = await futures_cooldown(config.db_path, row['symbol'])
    if reason:
        return None, reason, fresh
    async with paired_lock:
        reason = paper_entry_reason(fresh)
        fresh['operator_risk_limits'] = trading_controls.profile('paper')
        position = await paper.open(fresh) if reason == 'ENTRY_ALLOWED' else None
    return position, reason if position or reason != 'ENTRY_ALLOWED' else 'CAPACITY_OR_DUPLICATE', fresh


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
    if s == 'inventory_paper' and secondary:
        from .inventory_experiment import render
        return render(getattr(secondary,'inventory_paper',None),getattr(secondary,'inventory_source',None))
    if paired_paper and s in ('home','paper','strategy_stats','diary','capital','status','risk','export','strategies','paper_settings','paper_checks','paper_archive','paper_modes_more') and secondary:
        from .paper_console import render
        return await render(s,config.db_path,paper,scanner,risk,strategy_runtime,secondary)
    if paired_paper and s in ('dex', 'dex_paper','futures_detail') and secondary:
        from .paired_paper_view import render
        return await render(config.db_path, paper, scanner, risk, strategy_runtime,
                            getattr(secondary, 'dex_paired', None), secondary)
    if s == "home":
        banner = render_incident_banner(live_supervisor, live_stop)
        body = render_home(scanner, paper, risk, strategy_runtime, live_stop)
        return (banner + "\n\n" + body) if banner else body
    if s == "export":
        return render_export_center()
    if s == "risk":
        return render_risk_center(risk, live_supervisor, live_stop)
    if s == "status":
        return render_system_center(scanner, strategy_runtime, secondary)
    if s == "strategies":
        body = render_strategy_console(strategy_runtime)
        if secondary and secondary.dex_experiments and not paired_paper:
            from .dex_paper_experiments import render
            body += "\n\n" + render(secondary.dex_experiments, strategy_runtime.enabled)
        return body
    if s == "strategy_stats":
        return render_strategy_stats(await strategy_diary_summary(config.db_path))
    if s == "capital":
        return render_capital(bankroll)
    if s == "dex":
        if secondary and secondary.dex_experiments:
            from .dex_paper_experiments import render
            return render(secondary.dex_experiments, strategy_runtime.enabled)
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
            if paired_paper and getattr(secondary, 'dex_paired', None):
                text += render_dex_paper(secondary.dex_paired)
        return text
    if s == "dex_paper":
        if secondary and secondary.dex_experiments:
            from .dex_paper_experiments import render
            return render(secondary.dex_experiments, strategy_runtime.enabled)
        return render_dex_paper(secondary.dex_paper if secondary else None)
    if s == "dex_stress":
        return render_dex_stress(await build_dex_stress(config.db_path))
    if s == "dex_wallet":
        return await render_wallet(config.db_path)
    if s == "readiness":
        return render_readiness(
            await build_readiness(
                config.db_path,
                config.live_acceptance_path,
                config.live_exit_venues,
                dex_runtime_connected=bool(getattr(secondary, "dex_live", None)),
            )
        )
    if s == "walk_forward":
        return render_walk_forward(
            await build_walk_forward(
                config.db_path, max_gap=max(120, config.interval * 3)
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
    if s == "live_costs":
        return render_live_costs(await build_live_costs(config.db_path))
    if s == "fund_execution":
        return render_funding_execution(await build_funding_execution(config.db_path))
    if s in ("sf_execution", "ss_execution"):
        strategy = "spot_futures" if s == "sf_execution" else "spot_spot"
        return render_cash_execution(
            await build_cash_execution(config.db_path, strategy)
        )
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
        if paired_paper:
            names=sorted(set(private_clients)|set(spot_private_clients))
            return '🏦 <b>Биржи</b>\n\n'+('\n'.join(escape(x.upper())+' · ключ добавлен' for x in names) or 'Нет подключённых бирж.')
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


@dp.message(F.text.contains("SHORT?") | F.text.contains("LONG?") | F.caption.contains("SHORT?") | F.caption.contains("LONG?"))
async def accept_dex_signal(m: Message):
    if not m.from_user or not allowed(m.from_user.id):
        return
    text=m.text or m.caption or ''
    if paired_paper:
        origin=m.forward_origin
        if (origin is None or getattr(origin,'type',None)!='channel'
                or (getattr(origin.chat,'username','') or '').lower()!='mexcdexspreadtracker'):
            return await m.answer('Перешли исходный пост из @MexcDexSpreadTracker.')
        intake=getattr(secondary,'paired_signal_intake',None)
        if intake is None:
            return await m.answer('Приём DEX-сигналов пока недоступен.')
        status=await m.answer('🔎 Проверяю токен…')
        result=await intake.accept(dict(id=origin.message_id,text=text,timestamp=origin.date.timestamp()))
        from .tg_ui import button
        label=(escape(result.get('base','Токен'))+' · проверка в приоритете' if result['ok']
               else '⛔ '+escape(result['reason']))
        await status.edit_text(label,parse_mode='HTML',reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [button('📉 Сжатие','pair:show','primary')]]))
        return
    if not dual_paper and not focus_enabled("DEX_CHANNEL_SIGNALS_ONLY"):
        return await m.answer("Приём сигналов включается переменной DEX_CHANNEL_SIGNALS_ONLY=true.")
    engine = (getattr(secondary, "dex_experiments", {}).get("dex_channel") if dual_paper else getattr(secondary, "dex_paper", None))
    if dual_paper and not strategy_runtime.enabled.get("dex_paired" if paired_paper else "dex_channel", False):
        return await m.answer("Включи «DEX BUY ↔ CEX SHORT» в разделе «Стратегии», затем перешли свежий сигнал." if paired_paper else "Включи «Канал → MEXC» в разделе «Стратегии», затем перешли свежий сигнал.")
    signals = getattr(getattr(engine, "source", None), "signals", None)
    if signals is None:
        return await m.answer("Виртуальный DEX Paper ещё не готов.")
    origin = m.forward_origin
    if origin is None or getattr(origin, "type", None) != "channel" or (getattr(origin.chat, "username", "") or "").lower() != "mexcdexspreadtracker":
        return await m.answer("Перешли исходный свежий пост из @MexcDexSpreadTracker через кнопку пересылки Telegram. Скриншоты и скопированный текст не принимаются.")
    try:
        resolver = getattr(engine.source, "resolve_signal", None)
        if resolver:
            await resolver(text, origin.date.timestamp())
        signal = signals.add(text, engine.source.routes, origin.date.timestamp(), "MexcDexSpreadTracker")
    except ValueError as exc:
        return await m.answer(str(exc))
    if paired_paper:
        return await m.answer(f"Адрес {signal['base']} добавлен в подбор рынков парного Paper. Проверяем DEX BUY + CEX SHORT по свежим котировкам и всем расходам. Направление из поста не превращается в односторонний ордер; открытие пары не гарантировано.")
    await m.answer(f"Сигнал {signal['base']} {'LONG' if signal['side'] == 'buy' else 'SHORT'} принят для Paper. Вход будет только после проверки свежих котировок, расходов и стакана. Принятый сигнал не гарантирует открытие позиции.")


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
        "walk_forward",
        "execution_replay",
        "live_costs",
        "sf_execution",
        "ss_execution",
        "fund_execution",
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
        if paired_paper:
            from .paired_paper_profile import save_pause
            save_pause(config.runtime_state_path,scanner.paused)
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
            "paper_settings",
            "paper_checks",
            "paper_archive",
            "paper_modes_more",
            "futures_detail",
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
            "walk_forward",
            "execution_replay",
            "live_costs",
            "sf_execution",
            "ss_execution",
            "fund_execution",
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
        if paired_paper:
            from .paired_paper_profile import save_pause
            save_pause(config.runtime_state_path,scanner.paused)
        s = "home" if paired_paper else "status"
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


export_lock = asyncio.Lock()


async def send_export(message):
    if export_lock.locked():
        await message.answer("Выгрузка уже готовится. Повторно нажимать не нужно.")
        return
    async with export_lock:
        await message.answer("Готовлю облегчённый дневник: сделки, состояния и последние проверки. Без тяжёлого Excel.")
        try:
            from .compact_audit_export import build
            with tempfile.TemporaryDirectory(prefix="arbitrage-export-") as directory:
                archive = await build(config.db_path, directory)
                await message.answer_document(
                    FSInputFile(archive),
                    caption="Дневник CSV/ZIP: сделки всех режимов; состояния и последние проверки DEX. До 5000 последних строк на таблицу. Покрытие и ограничения — в manifest.json. База не изменена.",
                )
        except Exception as exc:
            log.exception("Compact audit export failed (%s)", type(exc).__name__)
            await message.answer("Не удалось выгрузить дневник: " + type(exc).__name__ + ". История сохранена. Пришли логи Railway с Compact audit export failed; не запускай экспорт много раз подряд.")


def keyboard_for(screen):
    if paired_paper:
        from .paper_console import keyboard
        result = keyboard(screen,strategy_runtime.enabled,scanner.paused)
        if result is not None:
            return result
    if paired_paper and screen in ('dex', 'dex_paper'):
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='⚙️ Парные режимы', callback_data='strategies'),
             InlineKeyboardButton(text='📈 Обе позиции', callback_data='paper')],
            [InlineKeyboardButton(text='📒 Дневник / экспорт', callback_data='export')],
            [InlineKeyboardButton(text='📨 Старая MEXC-история', callback_data='dex_mode:dex_channel'),
             InlineKeyboardButton(text='🔎 Старая Bybit-история', callback_data='dex_mode:dex_bybit')],
            [InlineKeyboardButton(text='↻ Обновить', callback_data='dex_paper'),
             InlineKeyboardButton(text='‹ Главное меню', callback_data='home')]])
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
        if paired_paper:
            return venue_keyboard(sorted(set(private_clients)|set(spot_private_clients)))
        return venue_keyboard(scanner.ids)
    if screen == "strategies":
        return strategy_menu(strategy_runtime.enabled)
    if screen in (
        "replay",
        "walk_forward",
        "sf_replay",
        "ss_replay",
        "fund_replay",
        "dex_replay",
        "execution_replay",
        "live_costs",
        "sf_execution",
        "ss_execution",
        "fund_execution",
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
        if screen in ("dex", "dex_paper") and secondary and secondary.dex_experiments:
            return InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📨 Канал → MEXC", callback_data="dex_mode:dex_channel", style="primary"),
                 InlineKeyboardButton(text="🔎 Bybit · автопоиск", callback_data="dex_mode:dex_bybit", style="primary")],
                [InlineKeyboardButton(text="⚙️ Включить / выключить", callback_data="strategies")],
                [InlineKeyboardButton(text="📒 Дневник / экспорт", callback_data="export")],
                [InlineKeyboardButton(text="🧪 Replay и проверки", callback_data="dex_replay"), InlineKeyboardButton(text="⛓ Кошелёк", callback_data="dex_wallet")],
                [InlineKeyboardButton(text="⏱ Задержки / fills", callback_data="dex_stress"), InlineKeyboardButton(text="🏁 Готовность", callback_data="readiness")],
                [InlineKeyboardButton(text="↻ Обновить", callback_data="dex_paper"), InlineKeyboardButton(text="‹ Главное меню", callback_data="home")],
            ])
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


@dp.callback_query(F.data.startswith("dex_mode:"))
async def dex_mode_page(q: CallbackQuery):
    if not allowed(q.from_user.id):
        return await q.answer("Нет доступа")
    name = q.data.split(":",1)[1]
    engines = getattr(secondary, "dex_experiments", {})
    if name not in engines:
        return await q.answer("Режим ещё не готов")
    from .dex_paper_experiments import render_mode
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="‹ Парные режимы" if paired_paper else "⚙️ Входы: включить / выключить",callback_data="dex_paper" if paired_paper else "strategy:"+name)],
        [InlineKeyboardButton(text="📒 Экспорт дневника",callback_data="export")],
        [InlineKeyboardButton(text="↻ Обновить",callback_data=q.data), InlineKeyboardButton(text="‹ Сравнение режимов",callback_data="dex_paper")],
    ])
    await safe_edit(q.message, render_mode(name, engines[name], strategy_runtime.enabled.get(name,False)), keyboard)
    await q.answer()


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
    keyboard = back_menu(parent='top')
    text = render_market_detail(name, row)
    if trading_controls.profile()['mode'] == 'manual' and name == 'futures_futures':
        token = trading_controls.issue(name, row)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f'Подтвердить {trading_controls.environment.upper()} вход', callback_data='enter:'+token)]] + keyboard.inline_keyboard)
        text += '\n\nПодтверждение действует 30 секунд. Перед входом цены, обе ноги и риск проверяются заново.'
    await safe_edit(q.message, text, keyboard)
    await q.answer()


@dp.callback_query(F.data.startswith('lab:'))
async def strategy_lab_action(q: CallbackQuery):
    if not allowed(q.from_user.id):
        await q.answer('Нет доступа', show_alert=True)
        return
    await q.answer()
    from .strategy_lab_view import render
    from .strategy_math import Invalid
    parts = q.data.split(':')
    screen = parts[1]
    try:
        if screen in ('prepare','confirm'):
            if not strategy_lab_runtime or trading_controls.environment!='paper' or scanner.paused:
                raise Invalid('FRESH_PAPER_SOURCE_REQUIRED')
            if screen=='prepare':
                import hashlib,secrets
                if strategy_lab.state['settings']['mode']!='manual':
                    raise Invalid('MANUAL_MODE_REQUIRED')
                keys=[key for key in strategy_lab_runtime.source.by_key
                      if hashlib.sha256(key.encode()).hexdigest()[:16]==parts[2]]
                if len(keys)!=1:
                    raise Invalid('ROUTE_UNAVAILABLE')
                fresh=await strategy_lab_runtime.refresh(keys[0])
                await strategy_lab.process(dict(id=secrets.token_hex(16),type='snapshot',snapshot=fresh),allow_entries=False)
                token=strategy_lab.issue(keys[0])
                decision=strategy_lab.state['latest'][keys[0]]['decision']
                plan=decision['plan']
                await safe_edit(q.message,
                    f'🧪 <b>Ручной Paper-вход</b>\n\nСценарный NET: {escape(plan["scenario_net"])} {escape(plan["currency"])}\n'
                    f'Полный резерв: {escape(plan["capital"])}\n\nЭто модельное исполнение. Подтверждение действует 30 секунд; затем цены проверяются ещё раз.',
                    InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Подтвердить Paper',callback_data='lab:confirm:'+token)],
                                                           [InlineKeyboardButton(text='‹ Назад',callback_data='lab:home')]]))
                return
            await strategy_lab.confirm(parts[2],strategy_lab_runtime.refresh)
            screen='positions'
        elif screen == 'export':
            import json
            with tempfile.TemporaryDirectory() as folder:
                target = Path(folder)/'strategy_lab_report.json'
                target.write_text(json.dumps(await strategy_lab.export(), ensure_ascii=False, indent=2), encoding='utf-8')
                await q.message.answer_document(FSInputFile(target), caption='Изолированный Paper/replay. Все fills модельные; это не результат основного торгового счёта.')
            return
        if screen == 'detail':
            screen = parts[2]
        elif screen == 'mode':
            await strategy_lab.configure(mode=parts[2])
            screen = 'home'
        elif screen == 'toggle':
            selected = set(strategy_lab.state['settings']['enabled'])
            key = parts[2]
            selected.symmetric_difference_update([key])
            await strategy_lab.configure(enabled=sorted(selected))
            screen = key
        elif screen == 'risk' and len(parts) == 4:
            options = dict(max_notional={'10','25','50','100'}, min_net={'0.25','0.5','1'},
                           max_hold={'1200','28800','86400'}, stop_loss={'0.5','1','2'})
            if parts[2] not in options or parts[3] not in options[parts[2]]:
                raise Invalid('RISK_OPTION_INVALID')
            await strategy_lab.configure(values={parts[2]:parts[3]})
            screen = 'risk'
        elif screen == 'close':
            import secrets
            await strategy_lab.process(dict(id=secrets.token_hex(16),type='request_close',position_id=parts[2]))
            screen = 'positions'
        text, keyboard = render(strategy_lab,screen)
        await safe_edit(q.message,text,keyboard)
    except (ValueError, KeyError, IndexError) as error:
        await q.message.answer(escape(str(error)[:190]),parse_mode='HTML')


@dp.message(F.document.file_name.endswith('.strategy-replay.jsonl'))
async def strategy_lab_upload(m: Message):
    if not m.from_user or not allowed(m.from_user.id):
        return
    import io
    from .strategy_lab_view import import_jsonl, render
    if not m.document.file_size or m.document.file_size > 2_000_000:
        await m.answer('Replay: нужен JSONL не больше 2 МБ.')
        return
    buffer = io.BytesIO()
    await m.bot.download(m.document,destination=buffer)
    try:
        results = await import_jsonl(strategy_lab,buffer.getvalue().decode('utf-8'),
                                     allow_entries=trading_controls.environment=='paper' and not scanner.paused)
        text, keyboard = render(strategy_lab)
        await m.answer(f'Обработано событий: {len(results)}.\n\n'+text,reply_markup=keyboard,parse_mode='HTML')
    except (ValueError,KeyError,TypeError,UnicodeError) as error:
        await m.answer('Replay остановлен: '+escape(str(error)[:200])+'. Уже записанные события сохранены; повтор использует те же IDs.',parse_mode='HTML')


@dp.callback_query(F.data.startswith('ctl:'))
async def trading_control_page(q: CallbackQuery):
    if not allowed(q.from_user.id):
        await q.answer('Нет доступа', show_alert=True)
        return
    from .trading_controls_view import render
    try:
        parts = q.data.split(':')
        if parts[1] == 'custom':
            return await numeric_prompt(q,'ctl',parts[2])
        if parts[1] == 'dex':
            dex = getattr(secondary, 'dex_paired', None)
            if not dex or trading_controls.environment != 'paper' or trading_controls.profile()['mode'] != 'manual':
                await q.answer('Выбери PAPER → Вручную. Парный DEX LIVE ещё не допущен.', show_alert=True)
                return
            import hashlib, json
            from .dex_virtual_paper_source import route_key
            buttons = [[InlineKeyboardButton(text=r.get('label', r['cex_symbol'])[:50],
                callback_data='dcard:'+hashlib.sha256(json.dumps(route_key(r)).encode()).hexdigest()[:16])]
                for r in [r for r in dex.source.routes if dex.source.venue_allowed(r['cex_venue'])][:12]]
            buttons.append([InlineKeyboardButton(text='‹ Назад', callback_data='pair:show')])
            await safe_edit(q.message, '🔎 <b>Выбери пару</b> · PAPER', InlineKeyboardMarkup(inline_keyboard=buttons))
            await q.answer()
            return
        if parts[1] == 'env':
            trading_controls.update(environment=parts[2])
        elif parts[1] == 'mode':
            trading_controls.update(mode=parts[2])
        elif parts[1] == 'set':
            value = int(parts[3]) if parts[2] == 'max_positions' else float(parts[3])
            trading_controls.update(**{parts[2]:value})
        apply_operator_paper_limits()
        while trading_controls.events:
            event = trading_controls.events[0]
            await diary.record_decisions([dict(ts=event['ts'], strategy='operator_control', symbol='*',
                buy='', sell='', action='CONFIG_CHANGE', reason='OPERATOR_UPDATE',
                before=event['before'], after=event['after'])])
            trading_controls.events.pop(0)
        text, keyboard = render(trading_controls, config.live_enabled and config.live_entry_enabled,
                                show_risk=parts[1] in ('risk','set','field'),
                                selected_key=parts[2] if parts[1] in ('set','field') else None)
        await safe_edit(q.message, text, keyboard)
        await q.answer('Сохранено' if parts[1]!='show' else '')
    except (ValueError, IndexError):
        await q.answer('Недопустимая настройка', show_alert=True)


@dp.callback_query(F.data == 'dismiss:signal')
async def dismiss_signal(q: CallbackQuery):
    if not allowed(q.from_user.id):
        return await q.answer('Нет доступа',show_alert=True)
    await q.answer('Пропущено')
    try:
        await q.message.delete()
    except Exception:
        await q.message.edit_text('Пропущено',reply_markup=None)


@dp.callback_query(F.data.startswith('enter:'))
async def manual_entry(q: CallbackQuery):
    if not allowed(q.from_user.id):
        await q.answer('Нет доступа', show_alert=True)
        return
    try:
        item = trading_controls.claim(q.data.split(':',1)[1])
    except ValueError:
        await q.answer('Карточка истекла или уже использована. Открой рынок снова.', show_alert=True)
        return
    await q.answer('Проверяю свежие данные')
    row = item['row']
    try:
        with trading_controls.manual_intent(item):
            if item['environment']=='paper' and item['strategy']=='spot_inventory':
                service=secondary.runtime.services['spot_inventory']
                if not strategy_runtime.enabled.get('spot_inventory'):
                    raise ValueError('STRATEGY_DISABLED')
                if not row.get('inventory_policy'):
                    raise ValueError('INVENTORY_ENTRY_CARD_STALE')
                position=await service.enter_route(row['asset'],row['buy'],row['sell'],expected_policy=row['inventory_policy'],
                    confirmation_guard=lambda:trading_controls.state['revision']==item['revision']
                        and trading_controls.environment=='paper' and trading_controls.profile('paper')['mode']=='manual')
                result='PAPER: '+position['status'] if position else 'PAPER: вход отклонён'
            elif item['environment'] == 'paper' and item['strategy'] == 'dex_paired':
                from .dex_virtual_paper_source import route_key
                dex = getattr(secondary, 'dex_paired', None)
                route = next((r for r in dex.source.routes if list(route_key(r)) == row['route_key']), None) if dex else None
                if not route:
                    raise ValueError('DEX_VIRTUAL_ROUTE_CHANGED')
                await dex.cycle(route, entry_enabled=True)
                sync_ui=getattr(secondary.runtime.services.get('dex_paired'),'sync_ui',None)
                if sync_ui is not None:
                    await sync_ui()
                result = 'DEX PAPER: ' + dex.state.get('last_decision', 'Проверь дневник')
            elif item['environment'] == 'paper' and paired_paper and item['strategy'] == 'futures_futures':
                position, reason, fresh = await paper_futures_entry(row)
                await diary.record_decisions([dict(fresh, strategy=item['strategy'],
                    paired_paper=True, manual=True, action='PAPER_OPEN' if position else 'SKIP', reason=reason)])
                if position:
                    await notify_strategy_open('futures_futures',position)
                result = 'Виртуальная пара открыта' if position else 'Вход отклонён: '+reason
            elif item['environment'] == 'live' and config.live_enabled and config.live_entry_enabled and live_entry_coordinator:
                async with live_monitor.lock:
                    outcome = await live_entry_coordinator.process([row])
                    result = 'LIVE: ' + str(outcome.get('status', 'Проверь журнал исполнения'))
                live_monitor.request_cycle()
            else:
                result = 'Вход заблокирован настройками запуска или недоступен для этого модуля.'
        await safe_edit(q.message, escape(result), menu())
    except Exception as exc:
        log.exception('Manual entry failed')
        await safe_edit(q.message, 'Вход не подтверждён. Проверь позиции и дневник; повторно нажимать старую карточку нельзя.\n'+escape(type(exc).__name__), menu())


@dp.callback_query(F.data.startswith('dcard:'))
async def manual_dex_card(q: CallbackQuery):
    if not allowed(q.from_user.id):
        await q.answer('Нет доступа', show_alert=True)
        return
    dex = getattr(secondary, 'dex_paired', None)
    if not dex or trading_controls.environment != 'paper' or trading_controls.profile()['mode'] != 'manual':
        await q.answer('Требуется PAPER → Вручную', show_alert=True)
        return
    import hashlib, json
    from .dex_virtual_paper_source import route_key
    route = next((r for r in dex.source.routes if hashlib.sha256(json.dumps(route_key(r)).encode()).hexdigest()[:16] == q.data.split(':')[1]), None)
    if not route:
        await q.answer('Маршрут обновился', show_alert=True)
        return
    await q.answer('Проверяю пару без открытия')
    result = await dex.source.entry(route)
    if not result.get('ok'):
        from .paired_paper_view import reason
        await safe_edit(q.message, 'Пара пока недоступна: '+escape(reason(result.get('reason', 'DEX_ENTRY_UNAVAILABLE'))), menu())
        return
    p = result['position']
    token = trading_controls.issue('dex_paired', dict(symbol=p['symbol'], route_key=list(route_key(route))))
    text = (f'DEX BUY ↔ CEX SHORT · PAPER\n{escape(p["symbol"])}\n'
            + f'Равный объём ног: {p["base_qty"]:.8g}\nDEX ${p["entry_cash"]:.2f} · CEX ${p["base_qty"]*p["entry_price"]:.2f}\n'
            + f'Расходы ≈${p["paired_policy"]["costs"]:.3f} · резерв funding ${p["paired_policy"]["funding_reserve"]:.3f}\n'
            + f'NET при частичном схождении: ${p["paired_policy"]["partial_net"]:.4f}\n'
            + 'Оценка модели · подтверждение 30 сек.')
    await safe_edit(q.message, text, InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='✓ Открыть', callback_data='enter:'+token,style='success'),
         InlineKeyboardButton(text='Пропустить', callback_data='dismiss:signal')],
        [InlineKeyboardButton(text='‹ Назад', callback_data='pair:show')]]))


@dp.callback_query(F.data.startswith("strategy:"))
async def strategy_toggle(q: CallbackQuery):
    if not allowed(q.from_user.id):
        await q.answer("Нет доступа", show_alert=True)
        return
    name = q.data.split(":", 1)[1]
    if name not in strategy_runtime.enabled:
        return await q.answer("Неизвестная стратегия")
    if paired_paper and getattr(secondary,'inventory_paper',None) and name not in ('spot_inventory','dex_paired'):
        return await q.answer('Исследовательский режим: новые входы — в Paper/replay. Старые позиции сопровождаются.',show_alert=True)
    if paired_paper and name in ('dex_channel', 'dex_bybit', 'cex_dex'):
        return await q.answer('Старый односторонний режим: новые входы отключены. История и сопровождение сохранены.', show_alert=True)
    if not dual_paper and focus_enabled("DEX_REFERENCE_FOCUS") and name != "cex_dex":
        return await q.answer("Сейчас включён отдельный эксперимент CEX↔DEX. Другие входы отключены переменной DEX_REFERENCE_FOCUS.")
    if name == "cex_dex":
        await safe_edit(q.message, await text_for("dex"), keyboard_for("dex"))
        return await q.answer()
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
    if paired_paper:
        from .paper_console import venue
        text,keyboard=venue(name,scanner.health.snapshot().get(name,{}),venue_controller.get(name))
        await safe_edit(q.message,text,keyboard)
        return await q.answer()
    await safe_edit(q.message, text, venue_control(name, venue_controller.get(name)))
    await q.answer()


async def notify_strategy_open(strategy,position):
    if notification_bot is None:
        return
    p=position if isinstance(position,dict) else vars(position)
    symbol=p.get('symbol') or p.get('base') or p.get('route','')
    try:
        await notification_bot.send_message(config.admin_id,
            '🧪 PAPER · позиция открыта\n'+escape(str(symbol))+'\n'+escape(describe_strategy(strategy,p)),parse_mode='HTML')
    except Exception:
        log.exception('Strategy open notification failed')


@dp.callback_query(F.data.startswith('inv:'))
async def inventory_action(q: CallbackQuery):
    if not allowed(q.from_user.id):
        return await q.answer('Нет доступа',show_alert=True)
    engine=getattr(secondary,'inventory_paper',None)
    if engine is None:
        return await q.answer('Эксперимент ещё не готов',show_alert=True)
    from .inventory_cards import handle_action
    await handle_action(q,engine,secondary.inventory_source,secondary.runtime.services['spot_inventory'],
                        trading_controls,lambda:not scanner.paused and strategy_runtime.enabled.get('spot_inventory'),safe_edit)


def transfer_position_keyboard(p):
    ident=p['id'];status=p['status'];rows=[]
    if status=='BUY_PENDING':
        rows.append([InlineKeyboardButton(text='Отменить покупку',callback_data=f'sig:position:{ident}:cancel_buy',style='danger')])
    if status in ('BASE_IN_TRANSIT','SELL_PENDING'):
        if p.get('sale_held'):
            rows.append([InlineKeyboardButton(text='▶️ Продолжить продажу',callback_data=f'sig:position:{ident}:resume_sale',style='primary')])
        else:
            rows.append([InlineKeyboardButton(text='⏸ Удержать продажу',callback_data=f'sig:position:{ident}:hold_sale')])
            if status=='SELL_PENDING':
                rows.append([InlineKeyboardButton(text='💱 Продать сейчас',callback_data=f'sig:position:{ident}:sell_now',style='primary')])
    rows.append([InlineKeyboardButton(text='↻ Состояние',callback_data=f'sig:position:{ident}:refresh'),
                 InlineKeyboardButton(text='‹ Назад',callback_data='sig:show')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.callback_query(F.data == 'balances:show')
async def account_balances_action(q: CallbackQuery):
    if not allowed(q.from_user.id):
        return await q.answer('Нет доступа',show_alert=True)
    await q.answer('Обновляю балансы')
    from .account_balances import snapshot, render
    rows=await snapshot(spot_private_clients,private_clients)
    await safe_edit(q.message,render(rows),InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='👛 Кошелёк',callback_data='wallet:show',style='primary')],
        [InlineKeyboardButton(text='↻ Обновить',callback_data='balances:show',style='primary'),
         InlineKeyboardButton(text='‹ Назад',callback_data='home')]]))


@dp.callback_query(F.data.startswith('wallet:'))
async def wallet_watch_action(q: CallbackQuery):
    if not allowed(q.from_user.id):
        return await q.answer('Нет доступа',show_alert=True)
    from .wallet_watch import snapshot,render
    if q.data=='wallet:edit':
        wallet_watch_pending[q.from_user.id]=(q.message.chat.id,q.message.message_id,time.time()+120)
        await q.answer()
        await safe_edit(q.message,'👛 <b>Адрес кошелька</b>\n\nПришли публичный EVM-адрес: 0x…\nТолько просмотр · 2 минуты на ввод.',InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='‹ Назад',callback_data='wallet:show')]]))
        return
    if q.data!='wallet:show':return await q.answer()
    wallet_watch_pending.pop(q.from_user.id,None)
    await q.answer('Читаю блокчейн')
    try:
        wallet=wallet_watch_store.get()
    except (ValueError,KeyError,OSError):
        wallet=None
    routes=[]
    service=getattr(getattr(secondary,'runtime',None),'services',{}).get('dex_paired')
    if service:
        routes=list(service.paper.source.routes)
        scan=getattr(service,'scanner',None)
        if scan:routes+=list(scan.catalog.values())
    text=render(wallet,await snapshot(wallet,routes)) if wallet else '👛 <b>Кошелёк</b>\n\nАдрес не добавлен.'
    await safe_edit(q.message,text,InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='↻ Обновить',callback_data='wallet:show',style='primary'),
         InlineKeyboardButton(text='Адрес',callback_data='wallet:edit')],
        [InlineKeyboardButton(text='‹ Балансы',callback_data='balances:show')]]))


@dp.message(F.text.regexp(r'^0x[0-9a-fA-F]{40}$'))
async def wallet_watch_address(m: Message):
    if not m.from_user or not allowed(m.from_user.id):return
    pending=wallet_watch_pending.get(m.from_user.id)
    if not pending or pending[0]!=m.chat.id or time.time()>pending[2]:return
    try:
        wallet=wallet_watch_store.set(m.text)
    except ValueError as exc:
        return await m.answer(str(exc))
    wallet_watch_pending.pop(m.from_user.id,None)
    # Keep the saved address out of the public repository and trading config.
    await m.bot.edit_message_text('👛 Адрес сохранён · только просмотр',chat_id=pending[0],message_id=pending[1],reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Балансы кошелька',callback_data='wallet:show',style='primary')]]))


@dp.callback_query(F.data.startswith('pairpos:'))
async def paired_position_action(q: CallbackQuery):
    if not allowed(q.from_user.id):
        return await q.answer('Нет доступа',show_alert=True)
    engine=getattr(secondary,'dex_paired',None)
    service=getattr(getattr(secondary,'runtime',None),'services',{}).get('dex_paired')
    sync=getattr(service,'sync_ui',None)
    if engine is None or sync is None:
        return await q.answer('PAPER-контур недоступен',show_alert=True)
    try:
        parts=q.data.split(':');_,ident,action=parts[:3]
        if action not in ('close','refresh','view','policy','history','auto','set') or not ident.isdecimal():
            raise ValueError('Недопустимое действие')
        if str(ident) not in engine.positions:
            await sync()
            return await q.answer('Сделка завершена · результат в дневнике',show_alert=True)
        if action=='close':
            await engine.request_exit(ident)
        if not hasattr(engine,'card_views'):engine.card_views={}
        if action in ('policy','history'):engine.card_views[str(ident)]=action
        elif action=='view':engine.card_views.pop(str(ident),None)
        elif action=='auto':
            p=engine.positions[str(ident)]
            await engine.update_management(ident,auto_profit=not p.get('operator_auto_profit',True))
        elif action=='set':
            if len(parts)!=5:raise ValueError('Некорректный параметр')
            await engine.update_management(ident,**{parts[3]:float(parts[4])})
        await q.answer('Выход запрошен' if action=='close' else 'Обновляю')
        if action in ('close','refresh'):await engine.cycle(entry_enabled=False)
        await sync()
    except ValueError as exc:
        await q.answer(str(exc)[:190],show_alert=True)


@dp.callback_query(F.data.startswith('audit:'))
async def decision_diagnostics_page(q: CallbackQuery):
    if not allowed(q.from_user.id):return await q.answer('Нет доступа',show_alert=True)
    from .decision_journal import Journal
    from .decision_diagnostics import render
    journal=Journal(config.db_path)
    try:
        text,keyboard=await render(journal,secondary,q.data,trading_controls,scanner.paused)
    except ValueError as exc:
        return await q.answer(str(exc)[:190],show_alert=True)
    await q.answer()
    await safe_edit(q.message,text,keyboard)


@dp.callback_query(F.data.startswith('pair:'))
async def paired_control_action(q: CallbackQuery):
    if not allowed(q.from_user.id):
        return await q.answer('Нет доступа',show_alert=True)
    engine=getattr(secondary,'dex_paired',None)
    if q.data=='pair:probe':
        service=secondary.runtime.services.get('dex_paired') if secondary else None
        scan=getattr(service,'scanner',None)
        evm=getattr(getattr(getattr(engine,'source',None),'provider',None),'evm',None)
        if (scan is None or evm is None or scanner.paused
                or not strategy_runtime.enabled.get('dex_paired',False)):
            return await q.answer('Сначала включи сканер и сними паузу',show_alert=True)
        if not evm.request_access_recheck():
            return await q.answer('Проверка уже идёт или прошло меньше 30 секунд',show_alert=True)
        scan.wake.set()
        return await q.answer('Повторная проверка доступа поставлена в очередь')
    if q.data=='pair:scan':
        if engine is None:
            return await q.answer('Сканер недоступен',show_alert=True)
        from .paired_console import scanner as scan_view
        service=secondary.runtime.services.get('dex_paired')
        text,keyboard=scan_view(engine,service,manual=trading_controls.environment=='paper' and trading_controls.profile('paper')['mode']=='manual')
        await q.answer()
        return await safe_edit(q.message,text,keyboard)
    try:
        if q.data != 'pair:show':
            if engine is None:
                raise ValueError('Парный PAPER-контур не загружен')
            if trading_controls.environment != 'paper':
                raise ValueError('Сначала выбери среду PAPER в настройках')
            if q.data == 'pair:toggle':
                enabled=not strategy_runtime.enabled.get('product_dex_paired',False)
                strategy_runtime.enabled['product_dex_paired']=enabled
                strategy_runtime.enabled['dex_paired']=enabled
                if enabled:
                    # Opt-in always starts with confirmation, regardless of
                    # the historical global PAPER profile's default.
                    trading_controls.update(mode='manual')
                strategy_toggles.save(strategy_runtime.enabled)
                await diary.record_decisions([dict(ts=time.time(),strategy='dex_paired',symbol='*',
                    buy='',sell='',action='CONFIG_CHANGE',reason='PAPER_ENTRY_TOGGLE',
                    enabled=strategy_runtime.enabled['dex_paired'])])
            elif q.data in ('pair:mode:observe','pair:mode:manual','pair:mode:auto'):
                trading_controls.update(mode=q.data.rsplit(':',1)[1])
                apply_operator_paper_limits()
            else:
                raise ValueError('Неизвестное действие')
            while trading_controls.events:
                event=trading_controls.events[0]
                await diary.record_decisions([dict(ts=event['ts'],strategy='operator_control',symbol='*',
                    buy='',sell='',action='CONFIG_CHANGE',reason='OPERATOR_UPDATE',
                    before=event['before'],after=event['after'])])
                trading_controls.events.pop(0)
    except ValueError as exc:
        return await q.answer(str(exc)[:190],show_alert=True)
    from .paired_console import render
    text,keyboard=render(engine,strategy_runtime.enabled,trading_controls,scanner.paused)
    await q.answer()
    await safe_edit(q.message,text,keyboard)


@dp.callback_query(F.data.startswith('sig:'))
async def market_signals_action(q: CallbackQuery):
    if not allowed(q.from_user.id):
        return await q.answer('Нет доступа',show_alert=True)
    feed=getattr(secondary,'market_signals',None)
    if feed is None:
        return await q.answer('Сканер сигналов отключён: MARKET_SIGNALS_ENABLED',show_alert=True)
    try:
        if q.data.startswith('sig:custom:'):
            return await numeric_prompt(q,'sig',q.data.split(':',2)[2])
        if q.data.startswith('sig:position:'):
            _,_,ident,action=q.data.split(':',3)
            p=feed.paper.positions.get(ident)
            if p is None:
                if feed.position_card:
                    await feed.paper.sync_cards(feed.position_card,feed.remove_position_card)
                return await q.answer('Сделка завершена · результат в дневнике',show_alert=True)
            if action!='refresh':
                if p is None:raise ValueError('Позиция уже закрыта')
                await feed.paper.control(ident,action)
            current=feed.paper.state.get('position') or feed.paper.state.get('last_position')
            if current is None or current['id']!=ident:raise ValueError('Карточка позиции устарела')
            if not feed.paper.state.get('position'):
                if feed.position_card:
                    await feed.paper.sync_cards(feed.position_card,feed.remove_position_card)
                return await q.answer('Сделка завершена · результат в дневнике',show_alert=True)
            await q.answer('Действие записано' if action!='refresh' else '')
            await safe_edit(q.message,feed.paper.card(current),transfer_position_keyboard(current))
            # The card remains bound to this message after operator edits.
            async with feed.paper.lock:
                state=__import__('copy').deepcopy(feed.paper.state)
                state['position_cards'][ident]=dict(message_id=q.message.message_id,text=feed.paper.card(current))
                await feed.paper.save(state,'POSITION_CARD_OPERATOR',dict(id=ident,action=action))
            return
        if q.data=='sig:toggle':
            await feed.store.toggle()
        if q.data.startswith('sig:mode:'):
            await feed.store.set_control('mode',q.data.split(':',2)[2])
        if q.data.startswith('sig:fund:'):
            await feed.paper.set_home(q.data.split(':',2)[2],feed.trade_venues)
        if q.data.startswith('sig:set:'):
            _,_,key,value=q.data.split(':',3)
            await feed.store.set_control(key,value)
        if q.data.startswith('sig:card:'):
            row=await feed.store.signal(q.data.split(':',2)[2])
            e=await feed.review(row['symbol'],row['buy'],row['sell'],read_accounts=True)
            e['id']=row['id']
            links=[[InlineKeyboardButton(text='↻ Обновить',callback_data=q.data)]]
            if e['transfer'] and not e['reasons'] and e['trade_configured']:
                token=await feed.store.issue(e)
                links.append([InlineKeyboardButton(text='✓ Открыть',callback_data='sig:paper:'+token,style='success'),
                              InlineKeyboardButton(text='Пропустить',callback_data='dismiss:signal')])
            links.append([InlineKeyboardButton(text='‹ Назад',callback_data='sig:show')])
            await q.answer()
            return await safe_edit(q.message,feed.format(e,include_code=False),InlineKeyboardMarkup(inline_keyboard=links))
        if q.data.startswith('sig:paper:'):
            control=await feed.store.control()
            if control['mode']!='manual':raise ValueError('Режим изменился: ручное подтверждение недоступно')
            route=await feed.store.claim(q.data.split(':',2)[2])
            if not (feed.entry_enabled and not scanner.paused):raise ValueError('Сканер на паузе')
            e=await feed.review(**route)
            if e.get('mode')!='manual':raise ValueError('Режим изменился: ручное подтверждение недоступно')
            if not e['trade_configured'] or e['reasons'] or e['transfer'] is None:
                raise ValueError('Новые данные не прошли проверку; позиция не открыта')
            await feed.paper.open(dict(signal=e,transfer=e['transfer']))
            if feed.position_card:
                await feed.paper.sync_cards(feed.position_card,feed.remove_position_card)
            await q.answer('Открыто · PAPER')
            from .transfer_console import render
            text,keyboard=await render(feed,'sig:show',scanner.paused)
            return await safe_edit(q.message,text,keyboard)

    except ValueError as exc:
        await q.answer(str(exc)[:190],show_alert=True)
        return
    from .transfer_console import render
    text,keyboard=await render(feed,q.data,scanner.paused)
    await q.answer()
    await safe_edit(q.message,text,keyboard)



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


async def notify_dex_open(position, label):
    if notification_bot is None:
        return
    p = position
    try:
        await notification_bot.send_message(config.admin_id,
            f"🟢 PAPER · позиция открыта\n{label}\n{p['symbol']} · {'LONG' if p['cex_side']=='buy' else 'SHORT'}\n"
            f"Размер: ${p['base_qty']*p['entry_price']:.2f}\nЦена модели: {p['entry_price']:.8g}\n"
            f"Потенциал NET при схождении: {p['entry_edge']:+.4f} USD\n"+describe_strategy('dex_paired',p))
    except Exception:
        log.exception("Paper open notification failed")


def paper_entry_reason(op):
    if paired_paper and getattr(secondary,'inventory_paper',None):
        return 'CONVERGENCE_EDGE_UNPROVEN_RESEARCH_ONLY'
    reason = operator_paper_reason('futures_futures', op)
    if reason:
        return reason
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
    if paired_paper:
        from .paired_paper_policy import futures_plan
        try:
            policy = futures_plan(op)
            policy = trading_controls.tighten_policy(policy)
            if policy['costs'] > policy['stop_usd'] * .5:
                return 'OPERATOR_STOP_TOO_TIGHT'
            if policy['partial_net'] < trading_controls.profile('paper')['min_net_usd']:
                return 'OPERATOR_NET_LOW'
        except ValueError as exc:
            return str(exc)
    elif op["hypothetical_edge"] < config.paper_entry_edge:
        return "BELOW_ENTRY_EDGE"
    if not paper.can_open(op):
        return "CAPACITY_OR_DUPLICATE"
    return "ENTRY_ALLOWED"


async def scanning():
    global latest
    while True:
        try:
            if paired_paper and not strategy_runtime.enabled.get('futures_futures') and not paper.positions:
                latest=[]
                strategy_runtime.update('futures_futures',[])
                await asyncio.sleep(config.interval)
                continue
            apply_operator_paper_limits()
            scanner.notional = trading_controls.profile()['notional_usd']
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
            if getattr(scanner, 'last_diagnostics', None):
                await diary.record_scan(scanner.last_diagnostics)
            if paired_paper:
                from .paired_futures_funding import attach
                funding_slots = asyncio.Semaphore(max(1, scanner.concurrency // 2))
                async def attach_calendar(row):
                    async with funding_slots:
                        await attach(row, scanner.funding, config.paper_max_seconds)
                await asyncio.gather(*(attach_calendar(row) for row in quotes))
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
                and config.live_enabled
                and config.live_entry_enabled
                and not scanner.paused
                and strategy_runtime.enabled["futures_futures"]
                and not trading_controls.entry_reason('live', 'futures_futures')
            ):
                async with live_monitor.lock:
                    await live_entry_coordinator.process(latest)
                live_monitor.request_cycle()
            closed = [] if paired_paper else await paper.mark_and_exit(quotes)
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
            from .scan_decisions import skipped_net_candidates
            decisions = skipped_net_candidates(quotes, paired_paper)
            for o in latest:
                reason = (
                    "CLOSED_THIS_CYCLE"
                    if o["symbol"] in {p.symbol for p in closed}
                    else paper_entry_reason(o)
                )
                p = None
                if reason == 'ENTRY_ALLOWED' and paired_paper:
                    from .paired_paper_profile import futures_cooldown
                    reason = await futures_cooldown(config.db_path, o['symbol']) or reason
                if reason == 'ENTRY_ALLOWED' and paired_paper:
                    try:
                        p, reason, fresh = await paper_futures_entry(o)
                        o.update(fresh)
                    except Exception as exc:
                        reason = str(exc) if isinstance(exc, ValueError) else 'PAIRED_BOOK_UNAVAILABLE'
                elif reason == 'ENTRY_ALLOWED':
                    p = await paper.open(o)
                decisions.append(
                    {
                        **o,
                        'paired_paper': paired_paper,
                        "strategy": "futures_futures",
                        "action": "PAPER_OPEN" if p else "SKIP",
                        "reason": reason,
                    }
                )
                if p:
                    log.info("Paper open %s %s/%s", p.symbol, p.buy, p.sell)
                    if paired_paper and notification_bot:
                        try:
                            await notification_bot.send_message(config.admin_id,
                            f"🧪 PAPER · парная позиция открыта\n{escape(p.symbol)}\n"
                            f"LONG {escape(p.buy.upper())} ↔ SHORT {escape(p.sell.upper())}\n"
                            f"Одинаковый объём: {p.base_qty:.8g}\n"
                            f"Размер ног: ${p.base_qty*p.entry_buy:.2f} / ${p.base_qty*p.entry_sell:.2f}\n"
                                "Вход по стаканам. Прибыль ещё не заработана.\n"+escape(describe_strategy('futures_futures',vars(p))))
                        except Exception:
                            log.exception('Paired Paper notification failed')
            await diary.record_decisions(decisions)
            log.info(
                "Cycle opportunities=%s paper=%s", len(latest), len(paper.positions)
            )
        except Exception:
            risk.on_error()
            log.exception("Scan failed")
            try:
                await diary.record_scan(dict(ts=time.time(), status='FAILED'))
            except Exception:
                log.exception('Failed to persist scanner failure')
        await asyncio.sleep(config.interval)


async def paired_monitor():
    from .paired_futures_quotes import refresh
    while True:
        try:
            quotes = []
            for position in list(paper.positions.values()):
                if position.exit_pending:
                    continue
                try:
                    quotes.append(await refresh(dict(symbol=position.symbol, buy=position.buy, sell=position.sell), scanner, position))
                    paper.monitor_reason = ''
                except Exception as exc:
                    paper.monitor_reason = str(exc) if isinstance(exc, ValueError) else 'PAIRED_BOOK_UNAVAILABLE'
            async with paired_lock:
                closed = await paper.mark_and_exit(quotes)
                for position in closed:
                    risk.on_paper_close(position.current_net_usd)
                    bankroll.apply(position.current_net_usd)
                    await ledger_add(config.db_path, time.time(), 'PAPER_NET',
                                     position.current_net_usd, str(position.id), note=position.symbol)
                    await notify_paper_close(position.symbol, position.current_net_usd,
                                             'Парный LONG ↔ SHORT · итог после расходов')
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('Paired Paper monitoring failed')
        await asyncio.sleep(2)


async def main():
    global spot_spot_live, funding_live
    global startup_text, private_clients, secondary, live_trades, live_stop, notification_bot, live_monitor, live_exit_coordinator, live_entry_coordinator, live_residual_coordinator
    global spot_private_clients, spot_future_live, strategy_lab_runtime
    live_stop = Stop(config.runtime_state_path + ".stop.json")
    pf = preflight_check(config)
    if not pf.ok:
        raise RuntimeError(render_preflight(pf))
    if pf.warnings:
        log.warning("%s", render_preflight(pf).replace("\\n", " | "))
    from .database_maintenance import ensure_not_restored

    ensure_not_restored(config.db_path)
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
    paired_task = None
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
        await strategy_lab.init()
        await ledger_init(config.db_path)
        await durable.init()
        await paper.restore()
        await restore_ledger(config.db_path, bankroll, risk)
        from .paired_paper_profile import require_monitoring as require_paired_monitoring
        await require_paired_monitoring(config.db_path, paired_paper)
        from .dex_paper_experiments import require_monitoring
        await require_monitoring(config.db_path, dual_paper)
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
        bootstrap_checkpoint('futures markets begin')
        await scanner.start()
        bootstrap_checkpoint('futures markets ready')
        if paired_paper:
            from .paired_futures_funding import Settlements
            paper.funding_history = Settlements(scanner.clients, scanner.funding, config.notional)
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
                and not trading_controls.entry_reason('live', strategy)
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

        dex_live = None

        async def monitor_update(summary, new_incidents):
            global live_trades
            live_book_priorities(
                await durable.active(),
                scanner.clients,
                getattr(secondary, "clients", {}),
            )
            live_trades = [RuntimeTrade(**row) for row in summary["runtime_trades"]]
            await live_exit_coordinator.process(summary)
            await live_residual_coordinator.process(summary)
            summary["closed"].extend(await spot_future_live.process(summary))
            summary["closed"].extend(await spot_spot_live.process(summary))
            if dex_live:
                summary["closed"].extend(await dex_live.process(summary))
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
                and not trading_controls.entry_reason('live', strategy, symbol)
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
            market_fallback=getattr(config, "live_entry_market_fallback", False),
            market_authority=lambda symbol, lv, sv: entry_authority(symbol, lv, sv)
            and market_fallback_accepted(config.live_acceptance_path, (lv, sv)),
        )
        live_entry_coordinator.halt = live_stop.stop
        live_entry_coordinator.operator_limits = lambda: trading_controls.profile('live')
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
        funding_live.operator_limits = lambda: trading_controls.profile('live')

        def dex_entry_authority(venue):
            summary = live_monitor.latest or {}
            return bool(
                config.live_enabled
                and not trading_controls.entry_reason('live', 'cex_dex')
                and config.live_entry_enabled
                and strategy_runtime.enabled.get("cex_dex", False)
                and not scanner.paused
                and not live_stop.stopped
                and venue in config.live_exit_venues
                and venue_controller.get(venue)["scan"]
                and config.live_no_withdraw_attested
                and not live_supervisor.kill.pairs
                and not live_supervisor.kill.check("", venue, venue).blocked
                and summary.get("private_verified") is True
                and summary.get("reconciled") is True
                and summary.get("unknown_orders") == 0
                and 0 <= time.time() - summary.get("ts", 0) <= 15
                and order_streams is not None
                and venue in order_streams.tasks
                and not order_streams.tasks[venue].done()
                and venue in order_streams.observed
                and venue not in order_streams.errors
                and live_accepted(config.live_acceptance_path, (venue,))
            )

        bootstrap_checkpoint('secondary markets begin')
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
            book_history=book_history if config.record_books else None,
            book_record_interval=config.public_stream_record_interval,
            public_clients=scanner.clients if paired_paper else None,
            dex_options=dict(
                live_enabled=config.live_enabled,
                entry_authority=dex_entry_authority,
                exit_authority=venue_exit_authority,
                acceptance_path=config.live_acceptance_path,
                bankroll=config.live_capital,
                budget=min(5, config.notional),
                minimum_net=config.live_min_net_edge_usd,
                max_seconds=min(3600, config.paper_max_seconds),
                target=config.paper_target_convergence,
                trailing=config.paper_trailing_drawdown,
                safety=str(config.notional * config.safety_buffer_pct / 100),
            ),
        )
        bootstrap_checkpoint('secondary markets ready')
        secondary.runtime.paused = lambda: scanner.paused
        live_monitor.cash_observer.dex = secondary.runtime.services.get(
            "dex_live_observation"
        )
        dex_live = getattr(secondary, "dex_live", None)
        if dex_live:
            live_monitor.cash_observer.dex = dex_live.marker

        async def secondary_rows(name, rows, entry_enabled):
            from .secondary_strategy_runtime import live_dispatch_enabled
            coordinator = {
                "spot_futures": spot_future_live,
                "spot_spot": spot_spot_live,
                "funding_arb": funding_live,
                "dex_live_candidates": dex_live,
            }.get(name)
            if entry_enabled and coordinator and live_dispatch_enabled(config, name) and not trading_controls.entry_reason('live', name):
                async with live_monitor.lock:
                    await coordinator.process_rows(rows)
                live_monitor.request_cycle()

        secondary.runtime.on_rows = secondary_rows
        if paired_paper:
            from .paired_paper_profile import migrate_toggles
            migrate_toggles(config.runtime_state_path, strategy_runtime, strategy_toggles)
        sf_cycle = secondary.runtime.services["spot_futures"]
        sf_cycle.allow_open = (
            lambda x: not scanner.paused
            and operator_paper_reason('spot_futures', x) is None
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
            and operator_paper_reason('spot_spot', x) is None
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
        ss.on_open = lambda p: notify_strategy_open('spot_spot',p)
        sf_cycle.on_open = lambda p: notify_strategy_open('spot_futures',p)
        if fp:
            fp.on_open = lambda p: notify_strategy_open('funding_arb',p)
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
                and operator_paper_reason('cex_dex', p) is None
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
                    f"CEX/DEX {'виртуальный' if dpaper.virtual else 'firm'} Paper • {position['cex_venue']} • {position.get('cex_side', '?')} • {position.get('exit_reason', 'UNKNOWN')}",
                )
                if getattr(dpaper.source, "signals", None):
                    dpaper.source.signals.closed(position)

            dpaper.on_closed = dex_closed
            if dpaper.virtual and focus_enabled("DEX_REFERENCE_FOCUS"):
                dpaper.source.signals = Signals(config.runtime_state_path + ".dex_signals.json")
        sf_cycle.on_closed = sf_closed
        if dual_paper:
            from .dex_paper_experiments import build as build_experiments
            secondary.dex_experiments = await build_experiments(
                secondary, strategy_runtime, config.db_path, config.runtime_state_path,
                notify_paper_close,
                lambda p: not scanner.paused and all(venue_controller.get(p["cex_venue"])[k] for k in ("paper", "scan")),
                notify_open=notify_dex_open,
            )
            from .dex_route_discovery import Discovery
            secondary.route_discovery = Discovery(secondary.dex_experiments, config.runtime_state_path + ".dex_routes.json")
            for engine in secondary.dex_experiments.values():
                engine.discovery = secondary.route_discovery
            secondary.dex_experiments["dex_channel"].source.resolve_signal = secondary.route_discovery.signal
            if paired_paper:
                for old in secondary.dex_experiments.values():
                    old.allow_open = lambda p: False
                from .dex_paired_experiment import build as build_paired
                await build_paired(secondary, strategy_runtime, config.db_path, notify_paper_close,
                    lambda p: not scanner.paused and operator_paper_reason('dex_paired', p) is None and all(venue_controller.get(p['cex_venue'])[k] for k in ('paper', 'scan')),
                    notify_open=notify_dex_open)
                secondary.dex_paired.operator_notional = lambda: trading_controls.profile('paper')['notional_usd']
                secondary.runtime.services['dex_paired'].auto_enabled = lambda: trading_controls.environment=='paper' and trading_controls.profile('paper')['mode']=='auto'
            await secondary.route_discovery.start()
            if focus_enabled("DEX_CHANNEL_PUBLIC_READER_ENABLED") and os.getenv('MARKET_SIGNALS_ENABLED','true').lower() not in ('true','1','yes','on'):
                from .dex_public_channel_reader import Reader as PublicChannelReader
                secondary.public_channel_reader = PublicChannelReader(
                    secondary.dex_experiments["dex_channel"],
                    lambda: strategy_runtime.enabled.get("dex_paired" if paired_paper else "dex_channel", False) and not scanner.paused,
                )
                secondary.dex_experiments["dex_channel"].channel_reader = secondary.public_channel_reader
                await secondary.public_channel_reader.start()
        if paired_paper:
            for name,service in secondary.runtime.services.items():
                if name in ('spot_futures','spot_spot','funding_arb','cex_dex','dex_channel','dex_bybit','dex_paired'):
                    service.idle_when_disabled = True
        import json
        lab_routes_path=Path(config.runtime_state_path+'.strategy_lab_routes.json')
        if lab_routes_path.exists():
            try:
                from .strategy_public_source import Source as StrategyPublicSource, Runtime as StrategyPublicRuntime
                route_registry=json.loads(lab_routes_path.read_text(encoding='utf-8'))
                lab_clients={v+':futures':c for v,c in scanner.clients.items()}
                lab_clients.update({v+':public':c for v,c in secondary.clients.items()})
                source=StrategyPublicSource(lab_clients,route_registry,scanner.funding)
                strategy_lab_runtime=StrategyPublicRuntime(strategy_lab,source,
                    allow_entries=lambda route: trading_controls.environment=='paper' and not scanner.paused
                    and all(venue_controller.get(b['venue'])['paper'] and venue_controller.get(b['venue'])['scan'] for b in route['books']))
                strategy_lab_runtime.start()
            except (ValueError,TypeError,KeyError,OSError) as error:
                log.exception('Optional strategy route registry unavailable; primary monitoring continues')
                await strategy_lab.source_error('registry',str(error),time.time())
        if strategy_lab_runtime is None:
            for position in list(strategy_lab.positions.values()):
                if position['plan']['evidence'].get('source_origin')=='public_capture':
                    await strategy_lab.source_error(strategy_lab.route_key(position['plan']),
                                                    'PUBLIC_CAPTURE_ROUTES_REQUIRED',time.time())
        bot = Bot(token=config.token)
        notification_bot = bot
        paired_engine=getattr(secondary,'dex_paired',None)
        if paired_engine is not None:
            from .paired_position_cards import Cards
            from aiogram.exceptions import TelegramBadRequest
            cards=Cards(paired_engine)
            async def deliver_pair(text,markup,message_id):
                if message_id:
                    try:
                        await bot.edit_message_text(text,config.admin_id,message_id,parse_mode='HTML',reply_markup=markup)
                        return message_id
                    except TelegramBadRequest as exc:
                        message=str(exc).lower()
                        if 'message is not modified' in message:return message_id
                        if 'message to edit not found' not in message:raise
                sent=await bot.send_message(config.admin_id,text,parse_mode='HTML',reply_markup=markup)
                return sent.message_id
            async def remove_pair(message_id):
                try:
                    await bot.delete_message(config.admin_id,message_id)
                except TelegramBadRequest as exc:
                    if 'message to delete not found' not in str(exc).lower():raise
            async def sync_pair():
                await cards.sync(deliver_pair,remove_pair)
            secondary.runtime.services['dex_paired'].sync_ui=sync_pair
            async def pair_candidate(candidate):
                if (trading_controls.environment!='paper' or trading_controls.profile('paper')['mode']!='manual'
                        or not strategy_runtime.enabled.get('product_dex_paired',False) or scanner.paused):
                    return
                p=candidate['position'];route=candidate['route']
                # Assess the same risk policy as confirmation, but never
                # call an execution method inside this temporary context.
                with trading_controls.manual_intent(dict(environment='paper',strategy='dex_paired',
                        row=dict(symbol=p['symbol']),revision=trading_controls.state['revision'])):
                    if operator_paper_reason('dex_paired',p) is not None:return
                import hashlib,json,aiosqlite
                from .dex_virtual_paper_source import route_key
                key=hashlib.sha256(json.dumps(route_key(route)).encode()).hexdigest()[:16]
                async with aiosqlite.connect(config.db_path) as db:
                    await db.execute('CREATE TABLE IF NOT EXISTS paired_candidate_alerts(route TEXT PRIMARY KEY,sent REAL)')
                    row=await (await db.execute('SELECT sent FROM paired_candidate_alerts WHERE route=?',(key,))).fetchone()
                    if row and 0<=time.time()-row[0]<300:return
                    text=(f'📉 <b>Сжатие · PAPER · {escape(p["symbol"])}</b>\n\n'
                          f'DEX BUY ↔ {escape(p["cex_venue"].upper())} SHORT\n'
                          f'Объём хеджа {p["base_qty"]:.8g}\n'
                          f'DEX ${p["entry_cash"]:.2f} · фьючерс ${p["base_qty"]*p["entry_price"]:.2f}\n'
                          f'Сценарный NET ${p["paired_policy"]["partial_net"]:.4f}\n\n'
                          f'Расходы ≈${p["paired_policy"]["costs"]:.3f}\n'
                          'Оценка модели · перед входом повторная проверка.')
                    await bot.send_message(config.admin_id,text,parse_mode='HTML',reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text='Проверить',callback_data='dcard:'+key,style='primary'),
                         InlineKeyboardButton(text='Пропустить',callback_data='dismiss:signal')]]))
                    await db.execute('INSERT INTO paired_candidate_alerts VALUES(?,?) ON CONFLICT(route) DO UPDATE SET sent=excluded.sent',(key,time.time()))
                    await db.commit()
            secondary.runtime.services['dex_paired'].notify_candidate=pair_candidate
            paired_engine.on_opened=None
            paired_engine.on_closed=None
        strategy_lab.on_open = lambda p: notify_strategy_open(p['plan']['strategy'],p['plan'])
        if paired_paper:
            from .inventory_experiment import build as build_inventory, migrate as migrate_inventory
            migrate_inventory(config.runtime_state_path,strategy_runtime,strategy_toggles,trading_controls)
            # Retire new inventory experiments; their open positions remain monitored.
            strategy_runtime.enabled['spot_inventory']=False
            strategy_toggles.save(strategy_runtime.enabled)
            async def inventory_notification(event,position):
                plan=position['plan']
                from .inventory_cards import entry_text,entry_keyboard,card_row
                if event=='CANDIDATE':
                    token=None
                    if trading_controls.environment=='paper' and trading_controls.profile('paper')['mode']=='manual':
                        row=card_row(plan,secondary.inventory_paper)
                        token=trading_controls.issue('spot_inventory',row)
                    await bot.send_message(config.admin_id,entry_text(plan),reply_markup=entry_keyboard(token),parse_mode='HTML')
                    return
                title={'OPEN':'Модельные исполнения','CLOSE':'Цикл завершён по модели','ALERT':'Задержка восстановления запасов'}[event]
                text=(f"📦 PAPER · {title}\n{escape(plan['asset'])} · {escape(plan['buy'])} → {escape(plan['sell'])}\n"
                      f"BASE куплено {float(position['bought']):.8g} / продано {float(position.get('sold',position['matched'])):.8g}\n"
                      f"Спред плана {float(plan['spread_pct']):.3f}%\n"+escape(position['thesis']))
                if event=='CLOSE':
                    text+=f"\nNET цикла модели: {float(position['net']):+.4f} USDT."
                rows=[[InlineKeyboardButton(text='📈 Данные и управление',callback_data='inv:pos:'+str(position['id']))]] if position['status']!='CLOSED' else []
                rows.append([InlineKeyboardButton(text='📦 Сделки',callback_data='inv:show')])
                await bot.send_message(config.admin_id,text,parse_mode='HTML',reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
            await build_inventory(secondary,config.db_path,trading_controls,strategy_runtime,
                                  inventory_notification,lambda:scanner.paused,venue_controller.get,
                                  network_clients=private_clients)
        if paired_engine is not None and getattr(secondary,'route_discovery',None) is not None:
            paired_engine.source.operator_profile=lambda:trading_controls.profile('paper')
            paired_engine.entry_block_reason=lambda p:operator_paper_reason('dex_paired',p)
            if paired_engine.entry_executor:
                paired_engine.entry_executor.allow_start=lambda:not scanner.paused and strategy_runtime.enabled.get('product_dex_paired',False) and strategy_runtime.enabled.get('dex_paired',False)
            from .paired_signal_intake import Intake
            secondary.paired_signal_intake=Intake(config.db_path,secondary.route_discovery,
                                                  secondary.runtime.services['dex_paired'])
        if os.getenv('MARKET_SIGNALS_ENABLED','true').lower() in ('true','1','yes','on'):
            from .market_signals import Scanner as MarketSignals
            from .channel_signal_review import Reviewer
            async def market_signal_notification(text):
                import re
                match=re.search(r'Код карточки: ([0-9a-f]{16})',text)
                text=re.sub(r'\nКод карточки: [0-9a-f]{16}','',text)
                markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                    text='Проверить',callback_data='sig:card:'+match[1],style='primary'),
                    InlineKeyboardButton(text='Пропустить',callback_data='dismiss:signal')]]) if match else None
                await bot.send_message(config.admin_id,text,parse_mode='HTML',
                                       disable_web_page_preview=True,reply_markup=markup)
            from .credentials import configured
            trade_venues={'gate' if v=='gateio' else v for v in configured() if v in spot_private_clients}
            # The secondary bundle reuses futures market clients. MEXC needs its
            # own spot adapter; reuse the already loaded Bybit spot account client.
            spot_shared={v:c for v,c in getattr(secondary,'clients',{}).items() if v!='mexc'}
            if 'bybit' in spot_private_clients:
                spot_shared['bybit']=spot_private_clients['bybit']
            feed=MarketSignals(config.db_path,market_signal_notification,
                               trade_venues=trade_venues,account_clients=spot_private_clients,
                               shared_clients=spot_shared,paper_venues={'mexc'})
            feed.venue_allowed=lambda venue,kind:not scanner.paused and venue_controller.get(venue)[kind]
            await feed.init()
            recovered_decisions=await feed.journal.recover_interrupted()
            log.info("DECISION_JOURNAL_READY strategies=A,B live=DISABLED recovered=%s",recovered_decisions)
            from .decision_diagnostics import Service as DiagnosticsService
            from .product_execution_status import inspect as execution_status
            secondary.runtime.add('decision_diagnostics',DiagnosticsService(feed.journal,
                lambda:execution_status(secondary,trading_controls,scanner.paused)))
            feed.actionable_only=True
            async def position_card(p,text,message_id):
                markup=transfer_position_keyboard(p)
                if message_id:
                    try:
                        await bot.edit_message_text(text,config.admin_id,message_id,parse_mode='HTML',reply_markup=markup)
                        return message_id
                    except Exception:
                        log.exception('PAPER position card edit failed; sending replacement')
                sent=await bot.send_message(config.admin_id,text,parse_mode='HTML',reply_markup=markup)
                return sent.message_id
            feed.position_card=position_card
            async def remove_position_card(message_id):
                from aiogram.exceptions import TelegramBadRequest
                try:
                    await bot.delete_message(config.admin_id,message_id)
                except TelegramBadRequest as exc:
                    if 'message to delete not found' not in str(exc).lower():
                        raise
            feed.remove_position_card=remove_position_card
            feed.channel_review=Reviewer(secondary)
            if getattr(secondary,'dex_paired',None) and getattr(secondary,'kyber_provider',None):
                from .dex_transfer_router import Router as DexTransfer
                dex_transfer=DexTransfer(feed,secondary.runtime.services['dex_paired'])
                await dex_transfer.init()
                dex_transfer.paused=lambda:scanner.paused
                secondary.runtime.add('dex_transfer',dex_transfer)
            secondary.market_signals=feed
            secondary.runtime.add('market_signals',feed)
            from .dex_public_channel_reader import Reader as PublicChannelReader
            channel_engine=secondary.dex_experiments.get('dex_channel')
            if channel_engine is None:
                from types import SimpleNamespace
                channel_engine=SimpleNamespace(path=config.db_path,clock=time.time,sql=lambda s:s)
            async def channel_post(post):
                await feed.queue_channel(post)
                intake=getattr(secondary,'paired_signal_intake',None)
                if intake is not None:
                    result=await intake.accept(post)
                    if result.get('ok') and not result.get('duplicate'):
                        log.info('PAPER_CHANNEL | candidate=%s | intake_ms=%s',result.get('base'),result.get('elapsed_ms'))
            secondary.public_channel_reader=PublicChannelReader(
                channel_engine,lambda:True,on_post=channel_post,report_only=True)
            if secondary.dex_experiments.get('dex_channel'):
                secondary.dex_experiments['dex_channel'].channel_reader=secondary.public_channel_reader
            await secondary.public_channel_reader.start()
        await secondary.runtime.start()
        await live_monitor.start()
        if paired_paper:
            paired_task = asyncio.create_task(paired_monitor())
        task = asyncio.create_task(scanning())
        bootstrap_checkpoint('Telegram polling begin')
        await dp.start_polling(bot)
    finally:
        if strategy_lab_runtime:
            await strategy_lab_runtime.stop()
            strategy_lab_runtime=None
        if paired_task:
            paired_task.cancel()
            await asyncio.gather(paired_task, return_exceptions=True)
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
