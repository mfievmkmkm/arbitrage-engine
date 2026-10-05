import asyncio
import logging
import time
from datetime import datetime, timezone
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from .config import config
from .db import Diary
from .engine import Scanner

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("arbitrage")
diary = Diary(config.db_path)
scanner = Scanner(config.exchanges, config.notional, config.max_age)
latest = []
dp = Dispatcher()

def menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔎 Возможности", callback_data="top"),
         InlineKeyboardButton(text="📔 Дневник", callback_data="diary")],
        [InlineKeyboardButton(text="🏦 Биржи", callback_data="exchanges"),
         InlineKeyboardButton(text="📡 Статус", callback_data="status")],
        [InlineKeyboardButton(text="⏸ Пауза", callback_data="pause"),
         InlineKeyboardButton(text="▶️ Продолжить", callback_data="resume")],
    ])

def allowed(user_id):
    return bool(config.admin_id and user_id == config.admin_id)

def fmt_top():
    if not latest:
        return "Пока нет подходящих наблюдений. Данные могут быть недоступны."
    lines = ["🔎 ОЦЕНКИ ПО ПУБЛИЧНЫМ СТАКАНАМ",
             "НЕ гарантированная прибыль; funding/закрытие не подтверждены.\n"]
    for x in latest[:8]:
        lines.append(
            f"{x['symbol']} | {x['buy']} LONG / {x['sell']} SHORT\n"
            f"Видимый: {x['raw']:.2f}% | Исполнимый: {x['executable']:.2f}%\n"
            f"Гипотетический после 4 комиссий: {x['hypothetical_edge']:.2f}%"
            f" (объём одной ноги ≈ ${x['notional']:.2f})\n")
    return "\n".join(lines)

async def text_for(section):
    if section == "top":
        return fmt_top()
    if section == "diary":
        count, last, best = await diary.summary()
        stamp = datetime.fromtimestamp(last, timezone.utc).strftime("%d.%m %H:%M UTC") if last else "—"
        return (f"📔 ДНЕВНИК НАБЛЮДЕНИЙ\nЗаписей: {count}\n"
                f"Последняя: {stamp}\n"
                f"Макс. гипотетическая оценка: {best:.2f}%" if best is not None
                else f"📔 ДНЕВНИК НАБЛЮДЕНИЙ\nЗаписей: {count}\nПоследняя: {stamp}")
    if section == "exchanges":
        return ("🏦 ПЛОЩАДКИ\n" +
                "\n".join(f"{name}: {'🟢' if name in scanner.clients else '🔴'}"
                          for name in scanner.ids) +
                "\n\nВсе площадки: только публичные данные. Реальная торговля выключена.")
    if section == "status":
        stamp = datetime.fromtimestamp(scanner.last_scan, timezone.utc).strftime(
            "%H:%M:%S UTC") if scanner.last_scan else "ещё не сканировали"
        return (f"📡 СТАТУС\nСканер: {'⏸ пауза' if scanner.paused else '🟢 работает'}"
                f"\nБирж подключено: {len(scanner.clients)}"
                f"\nПоследний цикл: {stamp}\nОбъём расчёта: ${config.notional:.2f}"
                f"\nОшибки: {scanner.errors or 'нет'}\nРеальные сделки: ОТКЛЮЧЕНЫ")
    return "Неизвестный раздел"

@dp.message(CommandStart())
async def start(message: Message):
    if not allowed(message.from_user.id):
        return
    await message.answer("⚡ АРБИТРАЖ — режим наблюдения\nРеальные сделки отключены.",
                         reply_markup=menu())

@dp.message(Command("top", "diary", "exchanges", "status", "pause", "resume"))
async def commands(message: Message):
    if not allowed(message.from_user.id):
        return
    section = message.text.split()[0].lstrip("/").split("@")[0]
    if section in ("pause", "resume"):
        scanner.paused = section == "pause"
        await message.answer("⏸ Остановлено" if scanner.paused else "▶️ Продолжено",
                             reply_markup=menu())
    else:
        await message.answer(await text_for(section), reply_markup=menu())

@dp.callback_query(F.data.in_({"top", "diary", "exchanges", "status", "pause", "resume"}))
async def callbacks(query: CallbackQuery):
    if not allowed(query.from_user.id):
        await query.answer("Нет доступа", show_alert=True)
        return
    section = query.data
    if section in ("pause", "resume"):
        scanner.paused = section == "pause"
        section = "status"
    await query.message.edit_text(await text_for(section), reply_markup=menu())
    await query.answer()

async def scanning():
    global latest
    while True:
        try:
            if not scanner.paused:
                latest = await scanner.scan()
                await diary.record([x for x in latest
                                    if x["hypothetical_edge"] >= config.min_edge])
                log.info("Cycle: %s observations", len(latest))
        except Exception:
            log.exception("Scan failed")
        await asyncio.sleep(config.interval)

async def main():
    if not config.token or not config.admin_id:
        raise RuntimeError("BOT_TOKEN and ADMIN_ID are required in .env")
    await diary.init()
    await scanner.start()
    bot = Bot(token=config.token)
    task = asyncio.create_task(scanning())
    try:
        await dp.start_polling(bot)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await scanner.close()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
