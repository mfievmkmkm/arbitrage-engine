"""Operator view for the two-leg PAPER strategy; never grants LIVE authority."""
from html import escape
from aiogram.types import InlineKeyboardMarkup
from .tg_ui import button


def render(engine, enabled, controls, paused):
    profile = controls.profile('paper')
    modes = {'manual': 'Вручную', 'auto': 'Авто', 'observe': 'Наблюдение'}
    routes = getattr(getattr(engine, 'source', None), 'routes', [])
    active = enabled.get('product_dex_paired', False) and enabled.get('dex_paired', False)
    out = ['📉 <b>Сжатие · PAPER</b>',
           'DEX BUY ↔ фьючерс SHORT · равный объём BASE',
           f'Входы: {"включены" if active else "выключены"} · {modes[profile["mode"]]}',
           f'До ${profile["notional_usd"]:g} на сторону · не более {profile["max_positions"]} пар\n'
           f'Минимум сценарного NET ${profile["min_net_usd"]:g}\n'
           f'Стоп пары ${profile["stop_loss_usd"]:g} · до {profile["max_hold_seconds"]:g} сек.',
           f'Маршрутов: {len(routes)} · открыто: {len(engine.positions) if engine else 0}']
    venues = sorted({r['cex_venue'] for r in routes})
    if venues:
        out.append('Маршруты данных: ' + escape(', '.join(venues)) + '\nВходы нового контура: Bybit')
    if not engine:
        out.append('Контур не загружен. Включение недоступно.')
    else:
        out.append('Для новых входов нужен тариф Bybit из API аккаунта. Публичный тариф не подтверждает допуск.')
    if paused:
        out.append('Пауза новых входов · открытые пары сопровождаются.')
    if controls.environment != 'paper':
        out.append('Выбрана другая среда · новые PAPER-входы заблокированы.')
    out.append('NET — сценарий сжатия после расходов, не обещание прибыли. '
               'DEX-котировки текущего контура — модель через агрегатор, не подтверждение исполнения на Uniswap.')
    rows = []
    if engine:
        rows.append([button('Выключить входы' if active else 'Включить вручную', 'pair:toggle', 'danger' if active else 'success')])
        rows.append([button(('✓ ' if profile['mode'] == m else '') + label, 'pair:mode:' + m,
                            'primary' if profile['mode'] == m else None) for m, label in (('manual', 'Вручную'), ('auto', 'Авто'))])
        if active and profile['mode'] == 'manual' and controls.environment == 'paper':
            rows.append([button('Проверить пары', 'ctl:dex', 'primary')])
    rows += [[button('Риск и выход', 'ctl:risk'), button('Позиции', 'paper')],
             [button('↻ Обновить', 'pair:show'), button('‹ Назад', 'strategies')]]
    return '\n\n'.join(out), InlineKeyboardMarkup(inline_keyboard=rows)
