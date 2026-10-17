"""Compact Russian PAPER cards. All action prices are indicative and refreshed."""
import time
from html import escape
from aiogram.types import InlineKeyboardMarkup
from .tg_ui import button
from .strategy_math import D

STATUS={'AWAITING_REBALANCE':'Две стороны исполнены, жду твоего решения',
        'REBALANCE_PENDING':'Восстановление запасов: переводы в пути',
        'REBALANCE_DELAYED':'Переводы задержаны',
        'RECOVERY_REQUIRED':'Аварийное закрытие остатка',
        'MANUAL_EXIT_PENDING':'Ручное закрытие выполняется частями',
        'CLOSED':'Цикл завершён'}


def card_row(plan,engine):
    return dict(asset=plan['asset'],symbol=plan['asset']+'/USDT',buy=plan['buy'],sell=plan['sell'],inventory_policy=engine.policy_fingerprint())


def entry_text(plan):
    ts=float(plan.get('quote_ts',time.time()))
    fees=D(plan.get('buy_fee',0))+D(plan.get('sell_fee',0))
    text=(f'🔎 <b>Кандидат · PAPER · {escape(plan["asset"])}/USDT</b>\n\n'
          f'Купить на <b>{escape(plan["buy"])}</b> по ≈{D(plan["ask"]):.8g}\n'
          f'Продать запас на <b>{escape(plan["sell"])}</b> по ≈{D(plan["bid"]):.8g}\n'
          f'Объём BASE: {D(plan["quantity"]):.8g}\n'
          f'Покупка ${D(plan["cost"]):.4f} · продажа ${D(plan["proceeds"]):.4f}\n'
          f'Исполнимый спред: <b>{D(plan["spread_pct"]):.3f}%</b>\n\n'
          f'Комиссии двух spot-сделок: ${fees:.4f}\n'
          f'Восстановление запасов: ≈${D(plan.get("transfer_cost_usd",0)):.4f}\n'
          f'NET после модельных расходов: <b>{D(plan["projected_net"]):+.4f} USDT</b>\n'
          f'Отдельный запас безопасности: ${D(plan["safety"]):.4f}\n'
          f'NET за вычетом запаса: {D(plan["projected_net"])-D(plan["safety"]):+.4f}\n\n'
          'Зарабатываем на двух исполнениях одного BASE; после них восстанавливаем размещение средств. '
          'В ручном режиме переводы ждут твоего решения. Непокрытый остаток закрывается автоматически, возможен убыток.\n\n'
          f'Стаканы UTC: {time.strftime("%H:%M:%S",time.gmtime(ts))}. '
          'При подтверждении данные обновятся; цена и прибыль могут измениться. '
          'Это виртуальный кандидат, доход ещё не получен.')
    return text


def entry_keyboard(token=None):
    rows=[[button('Открыть PAPER-пару','enter:'+token,'primary')]] if token else [[button('Выбрать ручной режим','ctl:mode:manual')]]
    rows+=[[button('↻ Новые кандидаты','inv:card'),button('‹ Сделки','inv:show')]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def position_text(view):
    pos=view['position'];p=pos['plan']
    out=[f'📈 <b>PAPER · {escape(p["asset"])}</b> · #{pos["id"]}',
         f'Куплено {escape(p["buy"])} → продано из запаса {escape(p["sell"])}\n'
         +STATUS.get(pos['status'],escape(pos['status'])),
         f'Входной спред {D(p["spread_pct"]):.3f}%\n'
         f'Исполнено BASE: куплено {D(pos["bought"]):.8g}, продано {D(pos.get("sold",pos["matched"])):.8g}\n'
         f'Непокрытый остаток {D(pos["residual"]):.8g}']
    if view['kind']=='transfers':
        out.append(f'Результат исполнений после модельных расходов: {D(view["cycle_net"]):+.4f} USDT. '
                   'Окончательный NET будет признан после зачисления обоих переводов.')
        for t in pos['transfers']:
            status='зачислен' if t['status']=='CREDITED' else 'в пути'
            out.append(f'{escape(t["asset"])} {D(t["amount"]):.8g}: {escape(t["source"])} → {escape(t["destination"])} · {status}')
        out.append('«Запросить завершение» сохраняет запрос и продолжает сопровождение. '
                   'Перевод в пути не отменяется и не зачисляется досрочно.')
    else:
        a,b=view['book_a'],view['book_b']
        out.append(f'Для обратного закрытия сейчас:\n'
            f'Продать на {escape(p["buy"])} по bid ≈{D(a["bid"]):.8g}; объём {D(view["sell_a_quantity"]):.8g}\n'
            f'Купить на {escape(p["sell"])} по ask ≈{D(b["ask"]):.8g}; объём {D(view["buy_b_quantity"]):.8g}\n'
            f'Комиссии закрытия ≈${D(view["close_fees"]):.4f}')
        if view.get('close_net') is not None:
            out.append(f'<b>Итоговый NET, если полностью закрыть сейчас: {D(view["close_net"]):+.4f} USDT</b>')
        else:
            out.append('Полное закрытие сейчас не подтверждено глубиной или балансом. Запрос может исполняться частями; точный итог неизвестен.')
        if 'rebalance_net' in view:
            out.append(f'Альтернатива — восстановить запас переводами: оценка NET {D(view["rebalance_net"]):+.4f} USDT; '
                       'срок не меньше модельных 15 минут при доступной сети.')
        out.append('Обратное закрытие добавляет сделки и комиссии: исходный спред может быть потерян. '
                   'Ни отрицательный NET, ни режим наблюдения не запрещают выход. Подтверждение — по новым стаканам.')
    if pos.get('data_error'):
        out.append('Часть закрытия недоступна: проверь глубину, минимальный объём и свободный баланс. Запрос сохранён, сопровождение продолжается.')
    out.append('Все деньги виртуальные. Данные и комиссии модельные; результат не гарантирован.')
    return '\n\n'.join(out)[:3850]


def position_keyboard(view):
    rows=[]
    for action,token in view.get('tokens',{}).items():
        label={'close':'Закрыть обратными сделками','transfer':'Восстановить запас переводами','finish':'Запросить завершение'}[action]
        rows.append([button(label,'inv:confirm:'+token)])
    rows.append([button('↻ Пересчитать','inv:pos:'+str(view['position']['id'])),button('‹ Сделки','inv:show')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def handle_action(q,engine,source,service,controls,entry_enabled,edit):
    from .inventory_experiment import render,keyboard,human
    from .strategy_math import Invalid
    parts=q.data.split(':')
    committed=False
    try:
        if parts==['inv','demo']:
            await q.answer('Запускаю отдельный учебный сценарий')
            from .inventory_simulation import run,render as demo_text
            await edit(q.message,demo_text(await run()),InlineKeyboardMarkup(inline_keyboard=[[button('‹ Сделки','inv:show')]]))
            return
        if parts==['inv','card']:
            if controls.environment!='paper' or controls.profile('paper')['mode']!='manual':
                return await q.answer('Выбери PAPER → Вручную в управлении.',show_alert=True)
            if not entry_enabled():
                return await q.answer('Новые входы на паузе или режим выключен.',show_alert=True)
            await q.answer('Получаю свежие стаканы')
            snapshots=await source.capture()
            await engine.seed(snapshots)
            await engine.mark({k:b for x in snapshots for k,b in x['wallet_books'].items()})
            plans=[];skips=[]
            for s in snapshots:
                try:
                    p=engine.plan(s)
                except ValueError as exc:
                    a,b=s['books'];top=(D(b['bid'])/D(a['ask'])-1)*100
                    skips.append(f'{escape(s["asset"])} {escape(s["buy"])} → {escape(s["sell"])}: '
                                 f'ориентир по лучшим ценам {top:.3f}%; '+escape(human(str(exc))))
                    continue
                if all(source.allowed_venue(v) for v in (p['buy'],p['sell'])):
                    plans.append(p)
            plans.sort(key=lambda p:D(p['projected_net']),reverse=True)
            for i,p in enumerate(plans[:3]):
                row=card_row(p,engine)
                token=controls.issue('spot_inventory',row)
                if i==0:
                    await edit(q.message,entry_text(p),entry_keyboard(token))
                else:
                    await q.message.answer(entry_text(p),reply_markup=entry_keyboard(token),parse_mode='HTML')
            if not plans:
                text='🔎 <b>Допустимого PAPER-входа сейчас нет</b>\n\n'+'\n\n'.join(skips[:5])
                if not snapshots:
                    text+='\nНет подтверждённых стаканов. Подробности доступа к данным — на экране сделок.'
                text+='\n\nПорог не снижается автоматически. Учебный прогон доступен через «Проверить симулятор».'
                await edit(q.message,text[:3800],keyboard(engine))
            return
        if len(parts)==3 and parts[1]=='pos':
            await q.answer('Пересчитываю позицию')
            view=await engine.inspect_position(parts[2],source)
            await edit(q.message,position_text(view),position_keyboard(view));return
        if len(parts)==3 and parts[1]=='confirm':
            token=parts[2];ticket=await engine.peek_action(token);preview=ticket.get('preview',{})
            action=ticket['action'];labels={'close':'Закрыть обратными spot-сделками','transfer':'Восстановить запас переводами','finish':'Запросить завершение переводов'}
            text=f'🧪 <b>Подтверждение PAPER · #{escape(ticket["pid"])}</b>\n\n{labels[action]}\n'
            net=preview.get({'close':'close_net','transfer':'rebalance_net','finish':'cycle_net'}[action])
            text+=f'Последняя оценка итогового NET: {D(net):+.4f} USDT.\n' if net is not None else 'Полный итоговый NET сейчас неизвестен.\n'
            text+=('Будут новые стаканы. Цена может измениться; возможны убыток и частичное закрытие.' if action=='close'
                   else 'Зачисления продолжают ждать модельного срока и доступной сети. Кнопка не отменяет и не ускоряет перевод.')
            await edit(q.message,text,InlineKeyboardMarkup(inline_keyboard=[
                [button('Подтвердить PAPER-действие','inv:act:'+token,'primary')],
                [button('Отмена · пересчитать','inv:pos:'+ticket['pid'])]]))
            return await q.answer()
        if len(parts)==3 and parts[1]=='act':
            await q.answer('Проверяю и выполняю PAPER-действие')
            pos=await engine.request_action(parts[2],source)
            committed=True
            if pos['status']=='CLOSED':
                text=f'🧪 PAPER · цикл #{pos["id"]} закрыт\nИтоговый NET: {D(pos["net"]):+.4f} USDT.\nВсе расходы сохранены.'
                await edit(q.message,text,keyboard(engine))
            else:
                view=await engine.inspect_position(str(pos['id']),source)
                await edit(q.message,position_text(view),position_keyboard(view))
            return
        if len(parts)==4 and parts[1]=='set':
            await engine.configure(**{parts[2]:parts[3]})
        await edit(q.message,render(engine,source),keyboard(engine));await q.answer()
    except (ValueError,KeyError,TypeError,ArithmeticError) as exc:
        explanations={
            'INVENTORY_ACTION_EXPIRED':'Карточка истекла или уже использована. Пересчитай позицию.',
            'INVENTORY_ACTION_STALE':'Состояние позиции изменилось. Нужен новый расчёт.',
            'INVENTORY_POSITION_NOT_OPEN':'Цикл уже завершён или позиция не найдена.',
            'INVENTORY_ACTION_COST_CHANGED':'Сеть или расходы изменились. Пересчитай и подтверди заново.',
        }
        text=('Запрос сохранён, но свежие данные карточки недоступны. Проверь экран сделок.' if committed
              else explanations.get(str(exc),'Проверка не пройдена: '+human(str(exc))))
        await edit(q.message,'🧪 PAPER\n'+escape(text)+'\nСопровождение открытых позиций продолжается.',keyboard(engine))

    except Exception:
        import logging
        logging.getLogger('arbitrage').exception('Inventory operator view failed; inspect durable positions before retry')
        await edit(q.message,'🧪 PAPER\nНе удалось получить актуальные данные. Проверь экран сделок; '
                   'подтверждай действия только по новой карточке. Сопровождение продолжается.',keyboard(engine))
