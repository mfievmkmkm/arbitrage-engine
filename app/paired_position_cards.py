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
    entering=p.get('execution_phase') not in (None,'DONE')
    out=[f'📉 <b>PAPER · {escape(p["symbol"])}</b> · #{p["id"]}',
         f'DEX BUY: {p.get("dex_filled_qty",p["virtual_asset_qty"]):.8g}\n{escape(p["cex_venue"].upper())} SHORT: {p.get("hedged_base_qty",p["base_qty"]):.8g}',
         ('NET: ждём завершения хеджа' if entering else f'NET {"с funding" if known else "без сверки funding"}: <b>{p.get("net",0):+.4f} USD</b>\n'
         +('Свежая оценка' if fresh else 'Оценка устарела / ещё не получена')),
         f'Цель ${policy.get("target_usd",0):g} · стоп ${policy.get("stop_usd",0):g}']
    qty=p.get('virtual_asset_qty',p['base_qty'])
    if qty>0 and p.get('entry_cash') is not None and p.get('entry_price') is not None:
        dex_entry=p['entry_cash']/qty
        out.insert(2,f'{"План цены" if entering else "Вход"} DEX {dex_entry:.8g} · Futures {p["entry_price"]:.8g}\nСпред входа {(p["entry_price"]/dex_entry-1)*100:+.2f}%')
    if mark.get('exit_cash') is not None and qty>0 and mark.get('exit_price') is not None:
        dx=mark['exit_cash']/qty
        out.append(f'Закрытие DEX {dx:.8g} · Futures {mark["exit_price"]:.8g}\nСпред закрытия {(mark["exit_price"]/dx-1)*100:+.2f}%' if dx>0 else 'Нет цены DEX-выхода')
    out.append(f'Расходы вход/выход ${mark.get("entry_fees",p.get("entry_fees",0))+mark.get("exit_fees",0):.4f}\n'
               f'Funding ${mark.get("funding",0):+.4f} · удержание {max(0,int(now-p.get("opened_at",now)))} сек.\n'
               f'Ноги: {escape(p.get("status","OPEN"))}')
    if p.get('operator_exit_requested') is not None:
        out.append('Выход запрошен · ждём котировки и сверку результата.')
    if p.get('execution_phase') not in (None,'DONE'):
        out.append('Вход по этапам · '+escape(p.get('status',''))+'\nНепокрытая нога контролируется отдельно; NET до завершения хеджа не определён.')
        if p.get('execution_error'):out.append('Ожидание: '+escape(p['execution_error'][:150]))
    if p.get('status')=='EXIT_ACCOUNTING_PENDING':
        out.append('Выход смоделирован · итог ждёт сверки funding.')
    if p.get('data_reason') not in (None,'OK'):
        out.append('⏳ Ожидаем данные выхода')
    rows=[]
    if p.get('operator_exit_requested') is None and p.get('status')!='EXIT_ACCOUNTING_PENDING':
        rows.append([button('Закрыть пару',f'pairpos:{p["id"]}:close','danger')])
        rows.append([button('Параметры',f'pairpos:{p["id"]}:policy'),
                     button('Автовыход ✓' if p.get('operator_auto_profit',True) else 'Автовыход ○',f'pairpos:{p["id"]}:auto')])
    rows.append([button('История',f'pairpos:{p["id"]}:history')])
    rows.append([button('↻ Обновить',f'pairpos:{p["id"]}:refresh'),button('Настройки','pair:show')])
    return '\n\n'.join(out),InlineKeyboardMarkup(inline_keyboard=rows)


def management(p):
    policy=p.get('paired_policy',{})
    text=(f'⚙️ <b>Параметры пары #{p["id"]}</b>\n\n'
          f'Цель ${policy.get("target_usd",0):g} · стоп ${policy.get("stop_usd",0):g}\n'
          f'Время {policy.get("max_seconds",1200):g} сек.\n\n'
          'Автовыход управляет фиксацией прибыли. Защитный стоп и ограничение времени продолжают работать.')
    rows=[]
    for key,label,values in (('target_usd','Цель',(.25,.5,1)),('stop_usd','Стоп',(.5,1,2)),('max_seconds','Время',(300,600,1200))):
        rows.append([button(f'{label} {v:g}',f'pairpos:{p["id"]}:set:{key}:{v}') for v in values])
    rows.append([button('‹ Позиция',f'pairpos:{p["id"]}:view')])
    return text,InlineKeyboardMarkup(inline_keyboard=rows)


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
                    if row is None or row[0] not in ('CLOSED','CANCELLED_BEFORE_FILL'):continue
                    try:
                        await remove(card['message_id'])
                    except Exception:
                        continue
                    await db.execute('DELETE FROM paired_telegram_cards WHERE position_id=?',(ident,))
                for ident,p in positions.items():
                    view=getattr(engine,'card_views',{}).get(str(ident))
                    if view=='policy':text,keyboard=management(p)
                    elif view=='history':
                        journal=getattr(engine.source,'journal',None)
                        text=await journal.trace(p['trace_id']) if journal and p.get('trace_id') else 'История этой позиции — в выгрузке дневника.'
                        keyboard=InlineKeyboardMarkup(inline_keyboard=[[button('‹ Позиция',f'pairpos:{p["id"]}:view')]])
                    else:text,keyboard=render(p,engine.clock())
                    card=bindings.get(str(ident),{})
                    if card.get('text')==text:continue
                    try:
                        message_id=await deliver(text,keyboard,card.get('message_id'))
                    except Exception:
                        continue
                    await db.execute('INSERT INTO paired_telegram_cards VALUES(?,?,?) ON CONFLICT(position_id) DO UPDATE SET message_id=excluded.message_id,text=excluded.text',
                                     (str(ident),message_id,text))
                await db.commit()
