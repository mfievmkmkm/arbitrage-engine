import asyncio, logging
from datetime import datetime, timezone
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from .config import config
from .db import Diary
from .engine import Scanner
from .paper import PaperEngine

logging.basicConfig(level=logging.INFO)
log=logging.getLogger("arbitrage")
diary=Diary(config.db_path)
scanner=Scanner(config.exchanges,config.notional,config.max_age)
paper=PaperEngine(diary,config.paper_capital,config.max_paper_positions,
 config.paper_target_convergence,config.paper_trailing_drawdown,config.paper_max_seconds)
latest=[]; dp=Dispatcher()

def menu():
 return InlineKeyboardMarkup(inline_keyboard=[
  [InlineKeyboardButton(text="🔎 Возможности",callback_data="top"),InlineKeyboardButton(text="🧪 Paper",callback_data="paper")],
  [InlineKeyboardButton(text="📔 Дневник",callback_data="diary"),InlineKeyboardButton(text="📡 Статус",callback_data="status")],
  [InlineKeyboardButton(text="🏦 Биржи",callback_data="exchanges")],
  [InlineKeyboardButton(text="⏸ Пауза",callback_data="pause"),InlineKeyboardButton(text="▶️ Продолжить",callback_data="resume")]])

def allowed(uid):return bool(config.admin_id and uid==config.admin_id)

def fmt_top():
 if not latest:return "Пока нет подходящих наблюдений."
 lines=["🔎 ВОЗМОЖНОСТИ — PAPER DATA","Оценка, не гарантированная прибыль.\n"]
 for x in latest[:8]:
  lines.append(f"{x['symbol']} | {x['buy']} LONG / {x['sell']} SHORT\nИсполнимый вход: {x['executable']:.2f}% | после модели комиссий: {x['hypothetical_edge']:.2f}%\n")
 return "\n".join(lines)

def fmt_paper():
 if not paper.positions:return f"🧪 PAPER\nКапитал: ${paper.capital:.2f}\nАктивных позиций нет."
 lines=[f"🧪 PAPER • капитал ${paper.capital:.2f} • занято ${paper.used_capital:.2f}"]
 for p in paper.positions.values():
  conv=(1-p.current_spread/p.entry_spread)*100 if p.entry_spread else 0
  lines.append(f"\n{p.symbol}\n🟢 {p.buy} / 🔴 {p.sell}\nВход: {p.entry_spread:.2f}% → сейчас: {p.current_spread:.2f}%\nСхождение: {conv:.1f}%\nЕсли закрыть сейчас: {p.current_net_usd:+.4f} USD\nМаксимум: {p.best_net_usd:+.4f} USD")
 return "\n".join(lines)

async def text_for(s):
 if s=="top":return fmt_top()
 if s=="paper":return fmt_paper()
 if s=="diary":
  count,last,best=await diary.summary(); trades,pnl,wins=await diary.paper_stats()
  stamp=datetime.fromtimestamp(last,timezone.utc).strftime("%d.%m %H:%M UTC") if last else "—"
  return f"📔 ДНЕВНИК\nНаблюдений: {count}\nПоследнее: {stamp}\nЗакрыто paper: {trades}\nPaper NET: {pnl:+.4f} USD\nПрибыльных: {wins}"
 if s=="exchanges":return "🏦 ПЛОЩАДКИ\n"+"\n".join(f"{x}: {'🟢' if x in scanner.clients else '🔴'}" for x in scanner.ids)+"\n\nРеальная торговля выключена."
 if s=="status":
  stamp=datetime.fromtimestamp(scanner.last_scan,timezone.utc).strftime("%H:%M:%S UTC") if scanner.last_scan else "—"
  return f"📡 СТАТУС\nСканер: {'⏸' if scanner.paused else '🟢'}\nБирж: {len(scanner.clients)}\nПоследний цикл: {stamp}\nPaper: {len(paper.positions)}/{paper.max_positions}\nLIVE: ОТКЛЮЧЕН"
 return "Неизвестный раздел"

@dp.message(CommandStart())
async def start(m:Message):
 if allowed(m.from_user.id):await m.answer("⚡ ARBITRAGE ENGINE\nРежим: DISCOVERY + PAPER",reply_markup=menu())

@dp.message(Command("top","paper","diary","exchanges","status","pause","resume"))
async def commands(m:Message):
 if not allowed(m.from_user.id):return
 s=m.text.split()[0].lstrip("/").split("@")[0]
 if s in ("pause","resume"):scanner.paused=s=="pause";s="status"
 await m.answer(await text_for(s),reply_markup=menu())

@dp.callback_query(F.data.in_({"top","paper","diary","exchanges","status","pause","resume"}))
async def callbacks(q:CallbackQuery):
 if not allowed(q.from_user.id):await q.answer("Нет доступа",show_alert=True);return
 s=q.data
 if s in ("pause","resume"):scanner.paused=s=="pause";s="status"
 await q.message.edit_text(await text_for(s),reply_markup=menu());await q.answer()

async def scanning():
 global latest
 while True:
  try:
   if not scanner.paused:
    latest=await scanner.scan()
    await diary.record([x for x in latest if x["hypothetical_edge"]>=config.min_edge])
    closed=await paper.mark_and_exit(latest)
    for p in closed:log.info("Paper close %s net=%s",p.symbol,p.current_net_usd)
    for o in latest:
     if o["hypothetical_edge"]>=config.paper_entry_edge and paper.can_open(o):
      p=await paper.open(o)
      if p:log.info("Paper open %s %s/%s",p.symbol,p.buy,p.sell)
    log.info("Cycle opportunities=%s paper=%s",len(latest),len(paper.positions))
  except Exception:log.exception("Scan failed")
  await asyncio.sleep(config.interval)

async def main():
 if not config.token or not config.admin_id:raise RuntimeError("BOT_TOKEN and ADMIN_ID required")
 await diary.init();await paper.restore();await scanner.start()
 bot=Bot(token=config.token);task=asyncio.create_task(scanning())
 try:await dp.start_polling(bot)
 finally:
  task.cancel();await asyncio.gather(task,return_exceptions=True);await scanner.close();await bot.session.close()
if __name__=="__main__":asyncio.run(main())
