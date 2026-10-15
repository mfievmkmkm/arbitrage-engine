from aiogram.types import InlineKeyboardMarkup
from .tg_ui import button

MODE_NAMES = {'observe': 'Наблюдение', 'manual': 'Вручную', 'auto': 'Авто'}
CHOICES = {'notional_usd': (5, 10, 25, 50, 100), 'daily_loss_usd': (1, 2, 5),
           'max_positions': (1, 2, 3), 'min_net_usd': (.05, .1, .25, .5, 1),
           'max_spread_pct': (2, 5, 10), 'stop_loss_usd': (.5, 1, 2), 'max_hold_seconds': (300, 600, 1200)}
LABELS = {'notional_usd':'На ногу, USD', 'daily_loss_usd':'Стоп закрытого NET за день, USD',
          'max_positions':'Открытых пар', 'min_net_usd':'Минимум NET, USD',
          'max_spread_pct':'Максимум спреда, %', 'stop_loss_usd':'Стоп пары, USD', 'max_hold_seconds':'Удержание, секунд'}


def render(controls, live_configured=False, show_risk=False):
    p = controls.profile()
    text = (f'🎛 <b>Управление торговлей</b>\n\n'
            + f'Среда: <b>{controls.environment.upper()}</b>\nРежим: <b>{MODE_NAMES[p["mode"]]}</b>\n\n'
            + 'Наблюдение — сигналы без новых позиций.\n'
            + 'Вручную — выбор пары и подтверждение в карточке.\n'
            + 'Авто — вход только после стратегии и риск-проверок.\n\n'
            + '\n'.join(f'{label}: {p[key]:g}' for key,label in LABELS.items())
            + '\n\nЛимиты применяются к новым входам. Сопровождение открытых позиций сохраняется.'
            + '\nПороги стратегии и сертифицированные LIVE-лимиты действуют дополнительно.')
    text += '\nДневной стоп запрещает новые входы по закрытому NET. Цена стопа и точный размер убытка не гарантируются.'
    if controls.environment == 'live':
        text += '\n\n' + ('LIVE требует допуска аккаунтов и всех проверок исполнения.' if live_configured
                            else '⛔ LIVE заблокирован настройками запуска. Выбор среды не разрешает отправку ордеров.')
    rows = [[button(('✓ ' if controls.environment==e else '')+e.upper(), 'ctl:env:'+e) for e in ('paper','live')],
            [button(('✓ ' if p['mode']==m else '')+MODE_NAMES[m], 'ctl:mode:'+m) for m in ('observe','manual','auto')]]
    if not show_risk:
        rows += [[button('⚙️ Настроить риск', 'ctl:risk')],
                 [button('⚡ Возможности', 'top'), button('DEX · ручная пара', 'ctl:dex')],
                 [button('‹ Меню', 'home')]]
        return text, InlineKeyboardMarkup(inline_keyboard=rows)
    for key, choices in CHOICES.items():
        rows.append([button(('✓ ' if p[key]==v else '')+f'{v:g}', f'ctl:set:{key}:{v}') for v in choices])
    rows += [[button('⚡ Возможности', 'top'), button('DEX · ручная пара', 'ctl:dex')],
             [button('‹ Меню', 'home')]]
    # Labels on separate rows keep risk controls unambiguous.
    result = rows[:2]
    for (key,_), values in zip(CHOICES.items(), rows[2:2+len(CHOICES)]):
        result.extend([[button(LABELS[key], 'ctl:show')], values])
    result.extend(rows[-2:])
    return text, InlineKeyboardMarkup(inline_keyboard=result)
