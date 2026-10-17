"""Durable Telegram bindings; financial truth stays in the paired PAPER journal."""
import asyncio
import copy
from html import escape
import aiosqlite
from aiogram.types import InlineKeyboardMarkup
from .tg_ui import button


def render(p, now):
    mark=p.get('last_mark') or {}
    stamp=mark.get('ts')
    fresh=isinstance(stamp,(int,float)) and 0<=now-stamp<=15
    known=mark.get('funding_known') is True
    policy=p.get('paired_policy') or {}
    out=[f'📉 <b>PAPER · {escape(p["symbol"])}</b> · #{p["id"]}',
         f'DEX BUY: {p["virtual_asset_qty"]:.8g}\n{escape(p["cex_venue"].upper())} SHORT: {p["base_qty"]:.8g}',
         f'NET {"с funding" if known else "без сверки funding"}: <b>{p.get("net",0):+.4f} USD</b>\n'
         +('Свежая оценка' if fresh else 'Оценка устарела / ещё не получена'),
         f'Цель ${policy.get("target_usd",0):g} · стоп ${policy.get("stop_usd",0):g}']
    if p.get('operator_exit_requested') is not None:
        out.append('Выход запрошен · ждём котировки и сверку результата.')
    if p.get('status')=='EXIT_ACCOUNTING_PENDING':
        out.append('Выход смоделирован · итог ждёт сверки funding.')
    if p.get('data_reason') not in (None,'OK'):
        out.append('Данные: '+escape(str(p['data_reason'])))
    rows=[]
    if p.get('operator_exit_requested') is None and p.get('status')!='EXIT_ACCOUNTING_PENDING':
        rows.append([button('Закрыть пару',f'pairpos:{p["id"]}:close','danger')])
    rows.append([button('↻ Обновить',f'pairpos:{p["id"]}:refresh'),button('Настройки','pair:show')])
    return '\n\n'.join(out),InlineKeyboardMarkup(inline_keyboard=rows)


class Cards:
    def __init__(self, engine):
        self.engine=engine
        self.lock=asyncio.Lock()

    async def sync(self, deliver, remove):
        async with self.lock:
            engine=self.engine
            async with engine.lock:
                positions=copy.deepcopy(engine.positions)
            async with aiosqlite.connect(engine.path) as db:
                await db.execute('CREATE TABLE IF NOT EXISTS paired_telegram_cards(position_id TEXT PRIMARY KEY,message_id INTEGER,text TEXT)')
                bindings={str(p):dict(message_id=m,text=t) for p,m,t in await (await db.execute('SELECT position_id,message_id,text FROM paired_telegram_cards')).fetchall()}
                for ident,card in bindings.items():
                    if ident in positions:continue
                    row=await (await db.execute(engine.sql('SELECT status FROM cex_dex_paper WHERE id=?'),(ident,))).fetchone()
                    if row is None or row[0]!='CLOSED':continue
                    try:
                        await remove(card['message_id'])
                    except Exception:
                        continue
                    await db.execute('DELETE FROM paired_telegram_cards WHERE position_id=?',(ident,))
                for ident,p in positions.items():
                    text,keyboard=render(p,engine.clock())
                    card=bindings.get(str(ident),{})
                    if card.get('text')==text:continue
                    try:
                        message_id=await deliver(text,keyboard,card.get('message_id'))
                    except Exception:
                        continue
                    await db.execute('INSERT INTO paired_telegram_cards VALUES(?,?,?) ON CONFLICT(position_id) DO UPDATE SET message_id=excluded.message_id,text=excluded.text',
                                     (str(ident),message_id,text))
                await db.commit()
