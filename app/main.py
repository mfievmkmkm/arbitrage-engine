import asyncio, logging
from datetime import datetime, timezone
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from .config import config
from .db import Diary
from .engine import Scanner
from .paper import PaperEngine
from .reports import build_replay_report
from .risk import RiskGuard
from .private_factory import build_private_readers,close_clients
from .live_bootstrap import bootstrap
from .startup_runtime import evaluate as evaluate_runtime_startup
from .runtime_store import RuntimeStore
from .startup_report import render as render_startup
from .preflight import check as preflight_check,render as render_preflight
from .paper_campaign import status as campaign_status,render as render_campaign
from .live_supervisor import LiveSupervisor
from .operator_stop import StopController
from .live_control_view import render as render_live_control
from .live_commands import stop as command_stop,resume as command_resume
from .resume_evidence import collect as collect_resume
from .live_heartbeat import evaluate as heartbeat_eval
from types import SimpleNamespace
from .strategy_runtime import StrategyRuntime
from .multi_strategy_view import render as render_strategies
from .strategy_diary import init as strategy_diary_init,summary as strategy_diary_summary
from .strategy_stats_view import render as render_strategy_stats
from .bankroll_ledger import Ledger
from .capital_view import render as render_capital
from .strategy_observation import row as strategy_row
from .strategy_diary import record as strategy_diary_record

logging.basicConfig(level=logging.INFO)
log=logging.getLogger("arbitrage")
diary=Diary(config.db_path)
scanner=Scanner(config.exchanges,config.notional,config.max_age,config.universe_size,config.scan_batch_size,config.scan_concurrency,config.safety_buffer_pct,config.paper_max_seconds)
paper=PaperEngine(diary,config.paper_capital,config.max_paper_positions,
 config.paper_target_convergence,config.paper_trailing_drawdown,config.paper_max_seconds)
risk=RiskGuard(config.paper_capital,config.daily_stop_pct,config.max_engine_errors)
latest=[]; dp=Dispatcher(); startup_text='🚦 Startup ещё не выполнен'; private_clients={}; live_supervisor=LiveSupervisor(config.max_engine_errors); live_stop=StopController(); strategy_runtime=StrategyRuntime(); bankroll=Ledger(config.paper_capital)

def menu():
 return InlineKeyboardMarkup(inline_keyboard=[
  [InlineKeyboardButton(text="🔎 Возможности",callback_data="top"),InlineKeyboardButton(text="🧪 Paper",callback_data="paper")],
  [InlineKeyboardButton(text="📔 Дневник",callback_data="diary"),InlineKeyboardButton(text="🧠 Replay",callback_data="replay")],
  [InlineKeyboardButton(text="📡 Статус",callback_data="status"),InlineKeyboardButton(text="🛡 Риски",callback_data="risk")],
  [InlineKeyboardButton(text="🏦 Биржи",callback_data="exchanges"),InlineKeyboardButton(text="🚦 Startup",callback_data="startup")],
  [InlineKeyboardButton(text="🎯 Этап 5",callback_data="campaign"),InlineKeyboardButton(text="⚡ LIVE",callback_data="live")],
  [InlineKeyboardButton(text="🛑 LIVE STOP",callback_data="live_stop"),InlineKeyboardButton(text="🔐 LIVE RESUME",callback_data="live_resume")],
  [InlineKeyboardButton(text="🧭 Стратегии",callback_data="strategies"),InlineKeyboardButton(text="📊 Статистика",callback_data="strategy_stats")],
  [InlineKeyboardButton(text="💰 Капитал",callback_data="capital"),InlineKeyboardButton(text="⛓ DEX",callback_data="dex")],
  [InlineKeyboardButton(text="⏸ Пауза",callback_data="pause"),InlineKeyboardButton(text="▶️ Продолжить",callback_data="resume")]])

def allowed(uid):return bool(config.admin_id and uid==config.admin_id)

def fmt_top():
 if not latest:return "Пока нет подходящих наблюдений."
 lines=["🔎 ВОЗМОЖНОСТИ — PAPER DATA","Оценка, не гарантированная прибыль.\n"]
 for x in latest[:8]:
  lines.append(f"{x['symbol']} | {x['buy']} LONG / {x['sell']} SHORT\nИсполнимый вход: {x['executable']:.2f}% | NET: {x['hypothetical_edge']:.2f}% | funding: {x.get('funding_pct',0):+.3f}%\n")
 return "\n".join(lines)

def fmt_paper():
 if not paper.positions:return f"🧪 PAPER\nКапитал: ${paper.capital:.2f}\nАктивных позиций нет."
 lines=[f"🧪 PAPER • капитал ${paper.capital:.2f} • занято ${paper.used_capital:.2f}"]
 for p in paper.positions.values():
  conv=(1-p.current_spread/p.entry_spread)*100 if p.entry_spread else 0
  lines.append(f"\n{p.symbol}\n🟢 {p.buy} / 🔴 {p.sell}\nВход: {p.entry_spread:.2f}% → сейчас: {p.current_spread:.2f}%\nСхождение: {conv:.1f}%\nЕсли закрыть сейчас: {p.current_net_usd:+.4f} USD\nМаксимум: {p.best_net_usd:+.4f} USD")
 return "\n".join(lines)

async def text_for(s):
 if s=="strategies":return render_strategies(strategy_runtime)
 if s=="strategy_stats":return render_strategy_stats(await strategy_diary_summary(config.db_path))
 if s=="capital":return render_capital(bankroll)
 if s=="dex":return "⛓ DEX\nResearch/Paper foundations: ON\nWallet execution: OFF\nLIVE: LOCKED until dedicated acceptance"
 if s=="top":return fmt_top()
 if s=="paper":return fmt_paper()
 if s=="replay":
  rows,report=await build_replay_report(diary)
  if not rows:return "🧠 REPLAY\nПока недостаточно закрытых paper-сделок."
  b=rows[0]
  return f"🧠 REPLAY • исследовательский\nСделок: {b['trades']}\nЛучший кандидат: target {b['target']*100:.0f}% / trailing {b['trailing']*100:.0f}% / {b['seconds']//60} мин\nNET: {b['net']:+.4f} USD\nWin rate: {b['win_rate']:.1f}%\nMax DD: {b['max_drawdown']:.4f} USD\n\n⚠️ In-sample: параметры автоматически не меняются."
 if s=="diary":
  count,last,best=await diary.summary(); trades,pnl,wins=await diary.paper_stats()
  stamp=datetime.fromtimestamp(last,timezone.utc).strftime("%d.%m %H:%M UTC") if last else "—"
  return f"📔 ДНЕВНИК\nНаблюдений: {count}\nПоследнее: {stamp}\nЗакрыто paper: {trades}\nPaper NET: {pnl:+.4f} USD\nПрибыльных: {wins}"
 if s=="exchanges":
  health=scanner.health.snapshot()
  lines=[]
  for x in scanner.ids:
   h=health.get(x,{})
   lines.append(f"{x}: {'🟢' if x in scanner.clients else '🔴'} • success {h.get('success_pct',0):.0f}% • {h.get('latency_ms','—')} ms")
  return "🏦 ПЛОЩАДКИ\n"+"\n".join(lines)+"\n\nLIVE выключен."
 if s=="risk":
  rs=risk.state
  return f"🛡 RISK CENTER\nСтатус: {'🛑 HALT' if rs.halted else '🟢 NORMAL'}\nПричина: {rs.reason or '—'}\nОшибок подряд: {rs.consecutive_errors}/{risk.max_errors}\nPaper PnL сегодня: {rs.paper_daily_pnl:+.4f} USD\nDaily stop: -{risk.bankroll*risk.daily_stop_pct/100:.2f} USD\nLIVE: заблокирован до private reconciliation"
 if s=="startup":return startup_text
 if s=="campaign":return render_campaign(await campaign_status(diary))
 if s=="live":return render_live_control(live_supervisor,[],0,live_stop)
 if s=="status":
  stamp=datetime.fromtimestamp(scanner.last_scan,timezone.utc).strftime("%H:%M:%S UTC") if scanner.last_scan else "—"
  coverage=scanner.universe.coverage if scanner.universe else 0
  rs=risk.state
  return f"📡 СТАТУС\nUniverse: {coverage} рынков\nСканер: {'⏸' if scanner.paused else '🟢'}\nБирж: {len(scanner.clients)}\nПоследний цикл: {stamp}\nPaper: {len(paper.positions)}/{paper.max_positions}\nRisk: {'🛑 '+rs.reason if rs.halted else '🟢 OK'} • day {rs.paper_daily_pnl:+.4f}$\nLIVE: ОТКЛЮЧЕН"
 return "Неизвестный раздел"

@dp.message(CommandStart())
async def start(m:Message):
 if allowed(m.from_user.id):await m.answer("⚡ ARBITRAGE ENGINE\nРежим: DISCOVERY + PAPER",reply_markup=menu())

@dp.message(Command("top","paper","diary","replay","exchanges","status","risk","startup","campaign","live","live_stop","live_resume","strategies","strategy_stats","capital","dex","pause","resume"))
async def commands(m:Message):
 if not allowed(m.from_user.id):return
 s=m.text.split()[0].lstrip("/").split("@")[0]
 if s in ("pause","resume"):scanner.paused=s=="pause";s="status"
 if s=="live_stop":command_stop(live_stop);s="live"
 if s=="live_resume":
  ev=collect_resume(SimpleNamespace(safe=live_supervisor.restart_clean,reason="RESTART_UNSAFE"),live_supervisor.private_verified,live_supervisor.unknown_orders,heartbeat_eval(0,0,True,True),False)
  command_resume(live_stop,ev,live_supervisor.kill);s="live"
 await m.answer(await text_for(s),reply_markup=menu())

@dp.callback_query(F.data.in_({"top","paper","diary","replay","exchanges","status","risk","startup","campaign","live","live_stop","live_resume","strategies","strategy_stats","capital","dex","pause","resume"}))
async def callbacks(q:CallbackQuery):
 if not allowed(q.from_user.id):await q.answer("Нет доступа",show_alert=True);return
 s=q.data
 if s in ("pause","resume"):scanner.paused=s=="pause";s="status"
 if s=="live_stop":command_stop(live_stop);s="live"
 if s=="live_resume":
  ev=collect_resume(SimpleNamespace(safe=live_supervisor.restart_clean,reason="RESTART_UNSAFE"),live_supervisor.private_verified,live_supervisor.unknown_orders,heartbeat_eval(0,0,True,True),False)
  command_resume(live_stop,ev,live_supervisor.kill);s="live"
 await q.message.edit_text(await text_for(s),reply_markup=menu());await q.answer()

async def scanning():
 global latest
 while True:
  try:
   if not scanner.paused:
    latest=await scanner.scan();strategy_runtime.update("futures_futures",latest);await strategy_diary_record(config.db_path,[strategy_row({**x,"strategy":"futures_futures"}) for x in latest]);risk.on_success()
    await diary.record([x for x in latest if x["hypothetical_edge"]>=config.min_edge])
    closed=await paper.mark_and_exit(latest)
    for p in closed:risk.on_paper_close(p.current_net_usd);log.info("Paper close %s net=%s",p.symbol,p.current_net_usd)
    for o in latest:
     if risk.can_open_paper() and o["hypothetical_edge"]>=config.paper_entry_edge and paper.can_open(o):
      p=await paper.open(o)
      if p:log.info("Paper open %s %s/%s",p.symbol,p.buy,p.sell)
    log.info("Cycle opportunities=%s paper=%s",len(latest),len(paper.positions))
  except Exception:risk.on_error();log.exception("Scan failed")
  await asyncio.sleep(config.interval)

async def main():
 global startup_text,private_clients
 pf=preflight_check(config)
 if not pf.ok:raise RuntimeError(render_preflight(pf))
 if pf.warnings:log.warning("%s",render_preflight(pf).replace("\\n"," | "))
 await diary.init();await strategy_diary_init(config.db_path);await paper.restore();await scanner.start()
 readers,private_clients=build_private_readers();boot=await bootstrap(readers);stored=RuntimeStore(config.runtime_state_path).load();intent_states=await diary.order_intent_states();startup=evaluate_runtime_startup(stored,boot.snapshot,config.live_enabled,intent_states);startup_text=render_startup(startup,boot.snapshot,stored);log.info("%s",startup_text.replace("\\n"," | "))
 bot=Bot(token=config.token);task=asyncio.create_task(scanning())
 try:await dp.start_polling(bot)
 finally:
  task.cancel();await asyncio.gather(task,return_exceptions=True);await scanner.close();await close_clients(private_clients);await bot.session.close()
if __name__=="__main__":asyncio.run(main())
