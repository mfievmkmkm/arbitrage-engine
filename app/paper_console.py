"""Small Paper-only operator console; detailed evidence stays one level below."""
import time
from html import escape
import aiosqlite
from aiogram.types import InlineKeyboardMarkup
from .tg_ui import button
from .paired_paper_view import reason

LABELS = {'futures_futures':'Фьючерсы ↔ Фьючерсы', 'dex_paired':'DEX ↔ Фьючерсы',
          'spot_futures':'Спот ↔ Фьючерсы', 'spot_spot':'Спот ↔ Спот', 'funding_arb':'Funding'}


def main_menu(paused=False):
    return InlineKeyboardMarkup(inline_keyboard=[
        [button('📈 Сделки','paper','primary'),button('📊 Результат','strategy_stats','primary')],
        [button('🧭 Режимы','strategies'),button('📒 Дневник','diary')],
        [button('⚙️ Настройки','paper_settings'),button('▶ Продолжить' if paused else '⏸ Пауза','resume' if paused else 'pause','success' if paused else None)],
        [button('↻ Обновить','home')]])


def keyboard(screen, enabled, paused=False):
    if screen == 'home':
        return main_menu(paused)
    if screen == 'strategies':
        rows = [[button(('🟢 ' if enabled.get(k) else '⚪ ')+LABELS[k], 'strategy:'+k,
                         'success' if enabled.get(k) else None)] for k in ('futures_futures','dex_paired')]
        rows += [[button('Условия и пропуски','dex_paper'),button('Другие режимы','paper_modes_more')]]
    elif screen == 'paper_modes_more':
        rows = [[button(('🟢 ' if enabled.get(k) else '⚪ ')+LABELS[k], 'strategy:'+k,
                        'success' if enabled.get(k) else None)] for k in ('spot_futures','spot_spot','funding_arb')]
    elif screen == 'paper_settings':
        rows = [[button('🎛 Среда · режим · риск','ctl:show')],
                [button('🏦 Биржи','exchanges'),button('💼 Капитал и риск','capital')],
                [button('🔎 Проверки','paper_checks'),button('🗂 Старая история','paper_archive')]]
    elif screen == 'paper_checks':
        rows = [[button('Проверка запуска','startup'),button('Рынок','top')],
                [button('Исторический Replay','replay'),button('Задержки и fills','execution_replay')],
                [button('Стресс DEX','dex_stress')]]
    elif screen == 'paper_archive':
        rows = [[button('MEXC · старая модель','dex_mode:dex_channel'),button('Bybit · старая модель','dex_mode:dex_bybit')],
                [button('Выгрузить дневник','export')]]
    elif screen in ('diary','export'):
        rows = [[button('📤 Скачать CSV / ZIP','export','primary')]]
    elif screen in ('dex','dex_paper','futures_detail'):
        rows = [[button('Рынок','top'),button('Режимы','strategies')],
                [button('Дневник','diary')]]
    elif screen in ('paper','strategy_stats','capital','status','risk'):
        rows = [[button('Дневник','diary'),button('Режимы','strategies')]]
    elif screen in ('replay','execution_replay','dex_stress','startup'):
        rows = [[button('‹ Проверки','paper_checks')]]
    else:
        return None
    rows.append([button('↻ Обновить',screen),button('‹ Главное меню','home')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def summary(path, dex):
    async with aiosqlite.connect(path) as db:
        async with db.execute("SELECT COUNT(*),COALESCE(SUM(current_net_usd),0),COALESCE(SUM(current_net_usd>0),0),AVG(CASE WHEN current_net_usd>0 THEN current_net_usd END),AVG(CASE WHEN current_net_usd<0 THEN current_net_usd END) FROM paper_positions WHERE status='CLOSED' AND paired_policy IS NOT NULL") as c:
            count,net,wins,win_avg,loss_avg=await c.fetchone()
        async with db.execute("SELECT current_net_usd FROM paper_positions WHERE status='CLOSED' AND paired_policy IS NOT NULL ORDER BY closed_at DESC,id DESC LIMIT 5000") as c:
            history=await c.fetchall()
        async with db.execute("SELECT reason,COUNT(*) FROM (SELECT reason,payload FROM signal_decisions WHERE strategy='futures_futures' ORDER BY id DESC LIMIT 1000) WHERE CASE WHEN json_valid(payload) THEN json_extract(payload,'$.paired_paper') ELSE 0 END=1 GROUP BY reason ORDER BY COUNT(*) DESC LIMIT 3") as c:
            skips=await c.fetchall()
    peak=current=drawdown=0
    for (value,) in reversed(history):
        current+=value;peak=max(peak,current);drawdown=max(drawdown,peak-current)
    state=dex.state if dex else {}
    return dict(count=count,net=net,wins=wins,win_avg=win_avg or 0,loss_avg=loss_avg or 0,
                drawdown=drawdown,drawdown_limited=count>5000,skips=skips,
                dex_count=state.get('closed_count',0),dex_net=state.get('realized_net',0))


def _state(enabled, key):
    return '🟢 Включён' if enabled.get(key) else '⚪ Выключен'


def _fresh(mark, now):
    ts=(mark or {}).get('ts')
    return isinstance(ts,(int,float)) and 0 <= now-ts <= 15


def venue(name,health,modes):
    text=(f'🏦 <b>{escape(name.upper())}</b>\n\n'
          + f'Успешных запросов: {health.get("success_pct",0):.0f}%\n'
          + f'Задержка: {escape(str(health.get("latency_ms","—")))} мс\n\n'
          + 'Сканер и виртуальные входы переключаются отдельно. Открытые позиции сопровождаются при выключении входов.')
    rows=[[button(('🟢 ' if modes.get(k) else '⚪ ')+label,'vt:'+name+':'+k,'success' if modes.get(k) else None)
           for k,label in (('scan','Сканер'),('paper','Paper'))],
          [button('↻ Обновить','venue:'+name),button('‹ Биржи','exchanges')]]
    return text,InlineKeyboardMarkup(inline_keyboard=rows)


def positions(paper,bundle):
    out=['📈 <b>Виртуальные сделки</b>']
    now=time.time(); count=0
    for p in list(paper.positions.values())[:5]:
        count+=1
        status='Выход выполнен по модели · ждём funding' if p.exit_pending else ('🟢 Свежая оценка' if _fresh(p.last_mark,now) else '🟠 Оценка устарела')
        known=(p.last_mark or {}).get('funding_known', not bool(p.paired_policy))
        out.append(f'<b>{escape(p.symbol)}</b>\nLONG {escape(p.buy.upper())} ↔ SHORT {escape(p.sell.upper())}\n'
                   + f'Объём каждой ноги: {p.base_qty or p.notional/p.entry_buy:.8g}\n'
                   + f'{"NET" if known else "NET без сверки funding"}: <b>{p.current_net_usd:+.4f} USD</b>\n{status}')
    modules=[('DEX ↔ Фьючерсы',getattr(bundle,'dex_paired',None)),
             ('Старая DEX-модель',getattr(bundle,'dex_paper',None))]
    modules += [('Старая '+name,e) for name,e in bundle.dex_experiments.items()]
    for label,engine in modules:
        for p in list(getattr(engine,'positions',{}).values())[:3]:
            count+=1
            legs='DEX BUY ↔ CEX SHORT' if p.get('virtual_mode')=='paired' else 'Старая односторонняя позиция'
            known=(p.get('last_mark') or {}).get('funding_known',False)
            out.append(f'<b>{escape(p["symbol"])}</b> · {escape(label)}\n{legs} · {escape(p["cex_venue"].upper())}\n'
                       + f'{"NET" if known else "NET без сверки funding"}: <b>{p.get("net",0):+.4f} USD</b>\n'
                       + ('Выход ждёт funding' if p['status']=='EXIT_ACCOUNTING_PENDING' else ('Свежая оценка' if _fresh(p.get('last_mark'),now) else 'Оценка устарела')))
            if p.get('data_reason') not in (None, 'OK'):
                out.append('Данные выхода: ' + escape(reason(p['data_reason'])))
    for p in list(getattr(bundle.sf_paper,'positions',{}).values())[:3]:
        count+=1
        out.append(f'<b>{escape(p.base)}</b> · {escape(p.exchange)}\nСпот LONG ↔ Фьючерсы SHORT\nNET модели: {p.net:+.4f} USD')
    for label,module in (('Спот ↔ Спот',bundle.ss_paper),('Funding',bundle.funding_paper)):
        for p in list(getattr(module,'positions',{}).values())[:3]:
            count+=1
            out.append(f'<b>{escape(p["symbol"])}</b> · {label}\nNET модели: {p.get("net",0):+.4f} USD')
    if not count:
        out.append('Открытых сделок нет. Причины пропусков — в «Режимы → Условия и пропуски».')
    return _bounded(out)


def _bounded(parts):
    out=[]
    for part in parts:
        if len('\n\n'.join(out+[part]))>3600:
            out.append('Полные данные — в дневнике.');break
        out.append(part)
    return '\n\n'.join(out)


async def render(screen,path,paper,scanner,risk,runtime,bundle):
    dex=getattr(bundle,'dex_paired',None);enabled=runtime.enabled
    if screen=='paper':
        return positions(paper,bundle)
    if screen in ('capital','risk','status','paper_settings'):
        free=max(0,paper.budget()-paper.used_capital-paper.external_reserved())
        return ('⚙️ <b>Настройки Paper</b>\n\n'
                + f'Общий счёт: ${paper.budget():.2f} · свободно ${free:.2f}\n'
                + f'До ${scanner.notional:.0f} на ногу · полный резерв обеих ног\n'
                + f'Дневной лимит общего счёта: ${risk.bankroll*risk.daily_stop_pct/100:.2f}\n'
                + (f'Отдельный DEX-счёт: ${dex.budget():.2f} · дневной лимит $5\n' if dex else '')
                + f'Бирж онлайн: {len(scanner.clients)}/{len(scanner.ids)}\n'
                + ('⏸ Сканер на паузе' if scanner.paused else '🟢 Сканер работает')
                + ('\n⛔ '+escape(risk.state.reason) if risk.state.halted else '')
                + '\n\nПауза останавливает новые входы, открытые позиции сопровождаются. Виртуальный капитал не считается прибылью. Реальная торговля выключена.')
    if screen in ('strategies','paper_modes_more'):
        keys=('spot_futures','spot_spot','funding_arb') if screen=='paper_modes_more' else ('futures_futures','dex_paired')
        out=['🧭 <b>Режимы · виртуальные деньги</b>']
        for key in keys:
            out.append(f'<b>{LABELS[key]}</b>\n{_state(enabled,key)}')
        out.append('Нажми кнопку режима для переключения. Выключение запрещает новые входы и сохраняет сопровождение открытых позиций.')
        if screen=='paper_modes_more':out.append('Эти три режима используют общий счёт $250 и общий резерв. Спот ↔ Спот требует виртуального инвентаря на обеих биржах; без него входов нет.')
        return _bounded(out)
    if screen=='paper_checks':
        from .scan_diagnostics import render as scan_status
        return ('🔎 <b>Проверки</b>\n\n' + scan_status(scanner)
                + '\n\nВыгрузка содержит журнал всех завершённых циклов, включая пустые.\n'
                + 'Запуск · свежие стаканы · задержки и частичное исполнение · исторический Replay.\n\n'
                + 'Старая история и тестовые сценарии не подтверждают прибыльность новой версии. Решение о реальной торговле принимается отдельно.')
    if screen=='paper_archive':
        return '🗂 <b>Старая история</b>\n\nРезультаты прежних односторонних моделей сохранены отдельно. Новые входы отключены; открытые позиции продолжают сопровождаться. Они не входят в результат новых парных тестов.'
    stats=await summary(path,dex)
    if screen in ('diary','export'):
        return ('📒 <b>Дневник</b>\n\n'
                + f'Новые парные закрытия: {stats["count"]+stats["dex_count"]}\n'
                + 'В CSV / ZIP: сделки, оценки обеих ног, расходы, funding и причины входов/пропусков/выходов.\n\n'
                + 'До 5000 последних строк каждой таблицы; покрытие указано в manifest.json. Полная история остаётся в базе. Эта выгрузка не является полной резервной копией.\n\n'
                + 'Paper-цены и расходы — модель, не реальные fills аккаунта.')
    if screen=='strategy_stats':
        return ('📊 <b>Результат парного Paper</b>\n\n'
                + f'Общий NET новых двух режимов: <b>{stats["net"]+stats["dex_net"]:+.4f} USD</b>\n\n'
                + f'<b>Фьючерсы ↔ Фьючерсы</b>\nЗакрыто {stats["count"]} · прибыльных {stats["wins"]}\n'
                + f'NET {stats["net"]:+.4f} USD\nСредний плюс {stats["win_avg"]:+.4f} · средний минус {stats["loss_avg"]:+.4f}\n'
                + f'Просадка закрытых: ${stats["drawdown"]:.4f}' + (' · последние 5000' if stats['drawdown_limited'] else '')
                + f'\n\n<b>DEX ↔ Фьючерсы</b>\nЗакрыто {stats["dex_count"]} · NET {stats["dex_net"]:+.4f} USD\n\n'
                + 'Результат после расходов. Старая история и пополнение виртуального капитала сюда не включены. Другие тестовые режимы — отдельно в дневнике.')
    opens=len(paper.positions)+(len(dex.positions) if dex else 0)
    out=['🧪 <b>АРБИТРАЖ · PAPER</b>',
         f'Новый парный NET: <b>{stats["net"]+stats["dex_net"]:+.4f} USD</b>\n'
         + f'Закрыто {stats["count"]+stats["dex_count"]} · открыто {opens}',
         f'Фьючерсы ↔ Фьючерсы · {_state(enabled,"futures_futures")}\n'
         + f'DEX ↔ Фьючерсы · {_state(enabled,"dex_paired")}',
         f'Общий счёт ${paper.budget():.2f}' + (f' · DEX ${dex.budget():.2f}' if dex else ''),
         '⏸ Новые входы на паузе' if scanner.paused else f'🟢 Поиск работает · бирж {len(scanner.clients)}/{len(scanner.ids)}']
    if risk.state.halted:out.append('⛔ Входы общего счёта остановлены: '+escape(risk.state.reason))
    if len(scanner.clients)<2:out.append('Для основного режима нужны две доступные биржи.')
    if not opens and stats['skips']:out.append('Частая причина пропуска: '+escape(reason(stats['skips'][0][0])))
    out.append('Две встречные позиции · прибыль от сжатия спреда после расходов. Все деньги виртуальные.')
    return _bounded(out)
