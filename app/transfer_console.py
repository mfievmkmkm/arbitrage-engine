"""Short transfer strategy screens; detailed scan coverage stays on demand."""
from aiogram.types import InlineKeyboardMarkup
from .tg_ui import button

FIELDS={'budget':('Покупка, USD',(25,50,100)), 'min_spread':('Спред от, %',(2,5,10)),
        'max_spread':('Спред до, %',(10,15,20)), 'min_net':('NET от, USD',(.25,.5,1)),
        'stress_drop':('Стресс цены, %',(3,5,10)), 'daily_loss':('Дневной стоп, USD',(.5,1,1.25)), 'total_loss':('Стоп теста, USD',(1,2,2.5)), 'dex_deposit_fee_pct':('Ввод Bybit, %',(0,.1,.5)), 'mexc_deposit_fee_pct':('Ввод MEXC, %',(0,.1,.5))}


async def render(feed, data, paused):
    p=await feed.store.control();enabled=await feed.store.enabled();s=feed.paper.state
    field=data.split(':')[2] if data.startswith(('sig:field:','sig:set:')) else None
    if field in FIELDS:
        label,values=FIELDS[field]
        current=p.get(field)
        text=f'⚙️ <b>{label}</b>\n\nСейчас: <b>{format(current,"g") if current is not None else "не указан"}</b>'
        if field in ('dex_deposit_fee_pct','mexc_deposit_fee_pct'):text+='\nДопущение PAPER на 24 часа. Укажи тариф своей площадки.'
        rows=[[button(('✓ ' if p.get(field)==v else '')+f'{v:g}',f'sig:set:{field}:{v}','primary' if p.get(field)==v else None) for v in values],
              [button('Своё число','sig:custom:'+field,'primary')],
              [button('‹ Назад','sig:settings')]]
    elif data=='sig:settings':
        text='⚙️ <b>Перевод · параметры</b>'
        rows=[[button(label+': '+(format(p[key],'g') if p.get(key) is not None else 'не указан'),'sig:field:'+key)] for key,(label,_) in FIELDS.items()]
        rows.append([button('‹ Назад','sig:show')])
    elif data=='sig:details':
        text=feed.status()
        rows=[[button('‹ Назад','sig:show')]]
    else:
        text=(f'🔁 <b>Перевод · PAPER</b>\n\n'
              f'{dict(manual="Вручную",auto="Авто",observe="Наблюдение")[p["mode"]]} · входы {"включены" if enabled else "выключены"}\n\n'
              f'Покупка до <b>${p["budget"]:g}</b>\n'
              f'Спред {p["min_spread"]:g}–{p["max_spread"]:g}% · NET от ${p["min_net"]:g}\n'
              f'Открыто: {int(bool(s.get("position")))}\n\n'
              'Продажа после зачисления. Цена может измениться.\n'
              +('Bybit Spot ↔ DEX · MEXC Spot ↔ DEX\nТоргуют только подключённые адаптеры.' if hasattr(feed,'dex_transfer') else 'DEX-перевод недоступен.'))
        if paused:text+='\n⏸ Входы на паузе'
        rows=[[button('⏸ Выключить' if enabled else '▶ Включить','sig:toggle','danger' if enabled else 'success')],
              [button(('✓ ' if p['mode']==m else '')+label,'sig:mode:'+m,'primary' if p['mode']==m else None)
               for m,label in (('observe','Смотреть'),('manual','Вручную'),('auto','Авто'))],
              [button('⚙️ Параметры','sig:settings'),button('📈 Сделки','paper')]]
        if not s.get('home'):
            venues=sorted(feed.trade_venues)
            for i in range(0,len(venues),3):
                rows.append([button('Начать на '+v.upper(),'sig:fund:'+v) for v in venues[i:i+3]])
        rows += [[button('Состояние сканера','sig:details')],[button('‹ Назад','strategies')]]
    return text,InlineKeyboardMarkup(inline_keyboard=rows)
