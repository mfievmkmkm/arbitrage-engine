"""Compact Russian overview of the agreed virtual two-leg experiments."""
from html import escape
import aiosqlite
from .dex_paper_experiments import reason_text

REASONS = {
    'PAIRED_PARTIAL_NET_LOW':'При частичном схождении расходы не оставляют $0.25 NET',
    'PAIRED_FUNDING_CALENDAR_UNKNOWN':'Не подтверждён календарь funding обеих ног',
    'PAIRED_IMMEDIATE_COST_HIGH':'Обратный выход слишком дорогой для лимита риска',
    'PAIRED_REFERENCE_GAP_EXTREME':'Аномальный разрыв цен: требуется проверка идентичности',
    'PAIRED_BOOK_DEPTH_OR_AGE':'Нет свежих стаканов достаточной глубины для обеих ног',
    'PAIRED_NATIVE_QUANTITY_INVALID':'Не удалось согласовать количество контрактов обеих ног',
    'PAIRED_PUBLIC_FEE_UNKNOWN':'Не подтверждена комиссия площадки',
    'ENTRY_ALLOWED':'Парный вход разрешён',
    'CAPACITY_OR_DUPLICATE':'Капитал зарезервирован или этот актив уже открыт',
    'RISK_HALTED':'Новые входы остановлены лимитом риска',
    'PAIRED_ASSET_COOLDOWN':'Пауза по активу после предыдущего закрытия',
    'PAIRED_ASSET_LOSS_LIMIT':'Два убытка по активу за UTC-день: входы до следующего дня остановлены',
    'PAIRED_BOOK_UNAVAILABLE':'Не удалось получить стаканы для обеих ног',
    'DEX_VIRTUAL_GAS_UNKNOWN_INVALID':'Котировка не предоставила проверяемую стоимость газа',
    'DEX_VIRTUAL_GAS_BUDGET_LIMIT':'Газ выше лимита модели $0.25 на операцию',
    'DEX_VIRTUAL_TOKEN_TAX_UNSUPPORTED':'Не подтверждены нулевые налоги на обмен/перевод токена',
    'DEX_VIRTUAL_NOTIONAL_OR_RESIDUAL_LIMIT':'Размер хеджа или остаток токена превышает лимит',
    'PAIRED_NOTIONAL_CAP_EXCEEDED':'Свежий VWAP превышает лимит размера ноги',
    'DEX_PAIRED_BASE_QUANTUM_MISMATCH':'Шаг контракта биржи несовместим с количеством знаков токена',
}


def reason(value):
    return REASONS.get(value, reason_text(value))


async def render(path, paper, scanner, risk, runtime, dex, bundle):
    async with aiosqlite.connect(path) as db:
        async with db.execute("SELECT COUNT(*),COALESCE(SUM(current_net_usd),0),COALESCE(SUM(current_net_usd>0),0) FROM paper_positions WHERE status='CLOSED' AND paired_policy IS NOT NULL") as c:
            count, net, wins = await c.fetchone()
        async with db.execute("SELECT reason,COUNT(*) FROM (SELECT reason,payload FROM signal_decisions WHERE strategy='futures_futures' ORDER BY id DESC LIMIT 1000) WHERE CASE WHEN json_valid(payload) THEN json_extract(payload,'$.paired_paper') ELSE 0 END=1 GROUP BY reason ORDER BY COUNT(*) DESC LIMIT 3") as c:
            counts = await c.fetchall()
    out = ['🧪 <b>ПАРНЫЙ АРБИТРАЖ · PAPER</b>',
           'Две встречные позиции. Доход — сжатие спреда после расходов. Все режимы на виртуальных деньгах; LIVE выключен.',
           '<b>1 · Фьючерсы ↔ Фьючерсы</b>\n'
           + ('🟢 Новые входы включены' if runtime.enabled.get('futures_futures') else '⚪ Новые входы выключены')
           + f'\nОбщий счёт ${paper.budget():.2f} · до ${scanner.notional:.0f} на ногу · без увеличения плеча\n'
           + f'Закрыто новых парных: {count} · прибыльных {wins}\nNET новых парных: <b>{net:+.4f} USD</b>\n'
           + f'Открыто {len(paper.positions)} · резерв ${paper.used_capital:.2f}\n'
           + f'Бирж онлайн {len(scanner.clients)}/{len(scanner.ids)} · возможности {runtime.counts().get("futures_futures", 0)}']
    if risk.state.halted:
        out.append('⏸ Лимит риска: ' + escape(risk.state.reason))
    if len(scanner.clients) < 2:
        out.append('⚠️ Для Futures ↔ Futures нужны две доступные биржи.')
    from .scan_diagnostics import render as scan_status
    out.append(scan_status(scanner))
    if counts:
        out.append('Последние 1000 решений:\n' + '\n'.join(f'• {escape(reason(k))}: {n}' for k,n in counts))
    else:
        out.append('Ждём подходящий исполнимый спред. Проверки появятся в дневнике; сделки не открываются ради количества.')
    for p in list(paper.positions.values())[:2]:
        out.append(f'{escape(p.symbol)} · LONG {escape(p.buy)} ↔ SHORT {escape(p.sell)}\n'
                   + f'Количество каждой ноги {p.base_qty or p.notional/p.entry_buy:.8g} · NET {p.current_net_usd:+.4f} USD'
                   + (' · выход ждёт сверки funding' if p.exit_pending else ''))
    if paper.positions and getattr(paper, 'monitor_reason', ''):
        out.append('Данные сопровождения: ' + escape(reason(paper.monitor_reason)))
    if dex:
        service = bundle.runtime.services.get('dex_paired')
        s = dex.state
        out.append('<b>2 · DEX BUY ↔ CEX SHORT</b>\n'
                   + ('🟢 Новые входы включены' if runtime.enabled.get('dex_paired') else '⚪ Новые входы выключены')
                   + f'\nОтдельный виртуальный счёт ${dex.budget():.2f} · до $100 DEX + $110 CEX\n'
                   + f'Парных маршрутов {len(dex.source.routes)} · проверок {s.get("checked_count", 0)}\n'
                   + f'Закрыто {s.get("closed_count",0)} · NET <b>{s.get("realized_net",0):+.4f} USD</b>\n'
                   + f'Открыто {len(dex.positions)} · резерв ${dex.used_capital:.2f}\n'
                   + escape(reason(s.get('last_decision','Ожидает первой проверки'))))
        if service and service.unsupported:
            out.append('Solana и сети без полного учёта расходов: пока только подбор рынков, парные входы не разрешены.')
        if dex.positions and s.get('last_monitor_reason') not in (None, 'OK'):
            out.append('Сопровождение DEX: ' + escape(reason(s['last_monitor_reason'])))
        if s.get('day') == int(dex.clock()//86400) and s.get('day_net',0) <= -5:
            out.append('⏸ DEX: дневной лимит потерь $5 достигнут.')
        reader = getattr(bundle, 'public_channel_reader', None)
        if reader:
            out.append('Подбор рынков из канала: ' + escape(reader.status) + '. Это не счётчик парных сделок.')
    old_net = sum(e.state.get('realized_net',0) for e in bundle.dex_experiments.values())
    old_open = sum(len(e.positions) for e in bundle.dex_experiments.values())
    out.append(f'Старые односторонние эксперименты: NET {old_net:+.4f} USD · открыто {old_open}. Новые входы отключены, история сохранена.')
    out.append('Прогноз схождения и funding — ещё не прибыль. Другие парные режимы включаются отдельно в «Стратегиях».')
    return '\n\n'.join(out)
