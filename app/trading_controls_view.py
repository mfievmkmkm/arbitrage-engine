from aiogram.types import InlineKeyboardMarkup
from .tg_ui import button

MODE_NAMES={'observe':'Пауза входов','manual':'Вручную','auto':'Авто'}
CHOICES={'notional_usd':(5,10,25,50,100),'daily_loss_usd':(1,2,5),
         'max_positions':(1,2,3),'min_net_usd':(.05,.1,.25,.5,1),
         'max_spread_pct':(2,5,10),'stop_loss_usd':(.5,1,2),'max_hold_seconds':(300,600,1200)}
LABELS={'notional_usd':'На сторону, USD','daily_loss_usd':'Дневной стоп, USD',
        'max_positions':'Открытых пар','min_net_usd':'NET от, USD',
        'max_spread_pct':'Спред до, %','stop_loss_usd':'Стоп пары, USD','max_hold_seconds':'Время, сек.'}


def render(controls,live_configured=False,show_risk=False,selected_key=None):
    p=controls.profile();env=controls.environment.upper()
    if selected_key in CHOICES:
        text=f'🛡 <b>{LABELS[selected_key]}</b> · {env}\n\nСейчас: <b>{p[selected_key]:g}</b>'
        rows=[[button(('✓ ' if p[selected_key]==v else '')+f'{v:g}',f'ctl:set:{selected_key}:{v}','primary' if p[selected_key]==v else None) for v in CHOICES[selected_key]],
              [button('Своё число','ctl:custom:'+selected_key,'primary')],
              [button('‹ Назад','ctl:risk')]]
    elif show_risk:
        text=f'🛡 <b>Риск · {env}</b>\n\nВыбери параметр'
        rows=[[button(f'{label}: {p[key]:g}','ctl:field:'+key)] for key,label in LABELS.items()]
        rows.append([button('‹ Назад','paper_settings')])
    else:
        text=f'⚙️ <b>Торговля · {env}</b>\n\n{MODE_NAMES[p["mode"]]}'
        rows=[[button(label,'ctl:mode:'+m,'primary' if p['mode']==m else None) for m,label in MODE_NAMES.items()],
              [button('🛡 Риск','ctl:risk')],[button('‹ Назад','paper_settings')]]
    if controls.environment=='live' and not live_configured:text+='\n\n⛔ LIVE заблокирован'
    if controls.environment!='paper':
        rows.insert(0,[button('Вернуться в PAPER','ctl:env:paper','primary')])
    return text,InlineKeyboardMarkup(inline_keyboard=rows)
