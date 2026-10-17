"""Compact operator view; trading admission is handled separately."""
from aiogram.types import InlineKeyboardMarkup
from .tg_ui import button


def render(engine, enabled, controls, paused):
    p=controls.profile('paper')
    active=enabled.get('product_dex_paired',False) and enabled.get('dex_paired',False)
    mode={'manual':'Вручную','auto':'Авто','observe':'Наблюдение'}[p['mode']]
    text=(f'📉 <b>Сжатие · PAPER</b>\n\n'
          f'DEX BUY ↔ Bybit SHORT\n'
          f'{mode} · входы {"включены" if active else "выключены"}\n\n'
          f'На сторону: <b>${p["notional_usd"]:g}</b>\n'
          f'NET от ${p["min_net_usd"]:g} · стоп ${p["stop_loss_usd"]:g}\n'
          f'открыто: {len(engine.positions) if engine else 0}')
    if not engine:text+='\n\n⏳ Модуль недоступен'
    if paused:text+='\n\n⏸ Новые входы на паузе · позиции сопровождаются'
    if controls.environment!='paper':text+='\n\nВыбрана другая среда'
    rows=[]
    if controls.environment!='paper':
        rows.append([button('Вернуться в PAPER','ctl:env:paper','primary')])
    if engine:
        rows += [[button('⏸ Выключить' if active else '▶ Включить вручную','pair:toggle','danger' if active else 'success')],
                 [button(('✓ ' if p['mode']==m else '')+label,'pair:mode:'+m,'primary' if p['mode']==m else None)
                  for m,label in (('manual','Вручную'),('auto','Авто'))]]
        if active and p['mode']=='manual' and controls.environment=='paper':
            rows.append([button('🔎 Проверить пары','ctl:dex','primary')])
    rows += [[button('🛡 Риск','ctl:risk'),button('📈 Сделки','paper')],
             [button('‹ Назад','strategies')]]
    return text,InlineKeyboardMarkup(inline_keyboard=rows)
