"""Short transfer strategy screens; detailed scan coverage stays on demand."""
from aiogram.types import InlineKeyboardMarkup
from .tg_ui import button

FIELDS={'budget':('Покупка, USD',(10,25,50)), 'min_spread':('Спред от, %',(5,7.5,10)),
        'max_spread':('Спред до, %',(10,15,20)), 'min_net':('NET от, USD',(.25,.5,1)),
        'stress_drop':('Стресс цены, %',(3,5,10))}


async def render(feed, data, paused):
    p=await feed.store.control();enabled=await feed.store.enabled();s=feed.paper.state
    field=data.split(':')[2] if data.startswith(('sig:field:','sig:set:')) else None
    if field in FIELDS:
        label,values=FIELDS[field]
        text=f'⚙️ <b>{label}</b>\n\nСейчас: <b>{p[field]:g}</b>'
        rows=[[button(('✓ ' if p[field]==v else '')+f'{v:g}',f'sig:set:{field}:{v}','primary' if p[field]==v else None) for v in values],
              [button('‹ Назад','sig:settings')]]
    elif data=='sig:settings':
        text='⚙️ <b>Перевод · параметры</b>'
        rows=[[button(f'{label}: {p[key]:g}','sig:field:'+key)] for key,(label,_) in FIELDS.items()]
        rows.append([button('‹ Назад','sig:show')])
    elif data=='sig:details':
        text=feed.status()
        rows=[[button('‹ Назад','sig:show')]]
    else:
        text=(f'🔁 <b>Перевод · PAPER</b>\n\n'
              f'{"Вручную" if p["mode"]=="manual" else "Авто"} · входы {"включены" if enabled else "выключены"}\n\n'
              f'Покупка до <b>${p["budget"]:g}</b>\n'
              f'Спред {p["min_spread"]:g}–{p["max_spread"]:g}% · NET от ${p["min_net"]:g}\n'
              f'Открыто: {int(bool(s.get("position")))}\n\n'
              'Продажа после зачисления. Цена может измениться.\n'
              'DEX-перевод пока недоступен.')
        if paused:text+='\n⏸ Входы на паузе'
        rows=[[button('⏸ Выключить' if enabled else '▶ Включить','sig:toggle','danger' if enabled else 'success')],
              [button(('✓ ' if p['mode']==m else '')+label,'sig:mode:'+m,'primary' if p['mode']==m else None)
               for m,label in (('manual','Вручную'),('auto','Авто'))],
              [button('⚙️ Параметры','sig:settings'),button('📈 Сделки','paper')]]
        if not s.get('home'):
            venues=sorted(feed.trade_venues)
            for i in range(0,len(venues),3):
                rows.append([button('Начать на '+v.upper(),'sig:fund:'+v) for v in venues[i:i+3]])
        rows += [[button('Состояние сканера','sig:details')],[button('‹ Назад','strategies')]]
    return text,InlineKeyboardMarkup(inline_keyboard=rows)
