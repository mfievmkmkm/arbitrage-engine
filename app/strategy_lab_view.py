"""Small Russian UI. Research capability does not masquerade as a LIVE toggle."""
from html import escape
from aiogram.types import InlineKeyboardMarkup
from .tg_ui import button
from .strategy_catalog import CATALOG
from .strategy_math import D

MODE = {'observe':'Наблюдение','manual':'Вручную','auto':'Авто replay'}


def render(lab, screen='home'):
    state, settings = lab.state, lab.state['settings']
    rows = []
    if screen in CATALOG:
        spec = CATALOG[screen]
        selected = screen in settings['enabled']
        latest = [v for k,v in state['latest'].items() if v['snapshot']['strategy']==screen]
        last = latest[-1] if latest else None
        text = (f'🧭 <b>{escape(spec.title)}</b>\n\n{escape(spec.income)}\n\n'
                f'<b>Вход</b>\n{escape(spec.requirements)}\n\n'
                f'<b>Фиксация</b>\n{escape(spec.exit)}\n\n'
                'Расчётная модель: реализована.\n'
                + ('Текущий рыночный модуль: отдельный основной контур.\n' if spec.source.startswith('legacy') else 'Новый адаптер рыночного исполнения: не подключён.\n')
                + f'Здесь: изолированный Paper/replay. LIVE недоступен.\n'
                + f'Режим: {MODE[settings["mode"]]} · выбран: {"да" if selected else "нет"}\n')
        if last:
            text += '\nПоследний расчёт: '+escape(last['reason'])
            if last['decision']['allowed']:
                plan=last['decision']['plan']
                text += f'\nСценарный NET: {D(plan["scenario_net"]):+.4f} {escape(plan["currency"])}\nЭто условный сценарий, не полученная прибыль.'
                if settings['mode']=='manual' and last['snapshot'].get('source_origin')=='public_capture':
                    import hashlib
                    key=lab.route_key(last['snapshot'])
                    rows.append([button('Подготовить ручной Paper-вход','lab:prepare:'+hashlib.sha256(key.encode()).hexdigest()[:16])])
        errors=[v for k,v in state.get('source_errors',{}).items() if k.startswith(screen+':')]
        if errors:
            text+='\nИсточник недоступен: '+escape(errors[-1])
        if spec.family != 'observe':
            rows.append([button('☑ Выбрана для replay' if selected else 'Выбрать для replay','lab:toggle:'+screen)])
        rows.append([button('‹ Все стратегии','lab:catalog')])
    elif screen == 'catalog':
        text = ('🧭 <b>Все стратегии</b>\n\nУ каждой — собственные вход, NET и выход. '
                'Нажми название для условий и состояния.\n\n'
                'Новые модели проверяются в изолированном Paper/replay. Наличие модели не означает готовый рыночный адаптер или доказанную доходность.')
        rows = [[button(spec.title,'lab:detail:'+key)] for key,spec in CATALOG.items()]
    elif screen == 'risk':
        p=settings['policy']
        text = (f'🎛 <b>Риск изолированного replay</b>\n\n'
                f'До {p["max_notional"]} {p["currency"]} на ногу/старт цикла\n'
                f'Минимальный NET: {p["min_net"]}; запас: {p["safety"]}\n'
                f'Стоп пары: {p["stop_loss"]}; срок: {p["max_hold"]} сек.\n'
                f'До {settings["max_positions"]} позиций; дневной стоп новых входов: {settings["daily_loss"]}\n\n'
                'Каждый расчёт резервирует полный капитал и расходы. Старые позиции сохраняют свой план. '
                'Изменения здесь не меняют основной Paper/LIVE-профиль.')
        rows = [[button('Нога '+v,'lab:risk:max_notional:'+v) for v in ('10','25','50','100')],
                [button('NET '+v,'lab:risk:min_net:'+v) for v in ('0.25','0.5','1')],
                [button(label,'lab:risk:max_hold:'+v) for label,v in (('20 мин','1200'),('8 ч','28800'),('24 ч','86400'))],
                [button('Стоп '+v,'lab:risk:stop_loss:'+v) for v in ('0.5','1','2')]]
    elif screen == 'positions':
        parts=['📈 <b>Позиции изолированного replay</b>']
        for pos in list(lab.positions.values())[:6]:
            plan=pos['plan']; mark=pos.get('last_mark') or {}
            parts.append(f'<b>{escape(CATALOG[plan["strategy"]].title)}</b> · {escape(plan["route"])}\n'
                         f'{escape(pos["status"])} · резерв {D(plan["capital"]):.4f}\n'
                         + (f'NET последней оценки: {D(mark["net"]):+.4f}' if mark.get('net') is not None else 'Окончательный NET неизвестен')
                         + f'\nВремя оценки: {escape(str(mark.get("ts","—")))}'
                         + ('\nДанные недоступны: '+escape(pos['data_error']) if pos.get('data_error') else ''))
            if pos['status'] not in ('ATOMIC_PENDING', 'REBALANCE_PENDING', 'ENTRY_UNCERTAIN', 'EXIT_ACCOUNTING_PENDING'):
                rows.append([button('Запросить выход · '+pos['id'][:6],'lab:close:'+pos['id'])])
        if not lab.positions:
            parts.append('Открытых позиций нет.')
        text='\n\n'.join(parts)
    else:
        text = (f'🧪 <b>Стратегии · Paper/replay</b>\n\n'
                f'Режим: {MODE[settings["mode"]]}\n'
                f'Виртуальный бюджет: {state["capital"]} USDT\n'
                f'Закрытый NET модели: {D(state["realized"]):+.4f} USDT\n'
                f'Резерв: {lab.reserved:.4f} · открыто: {len(lab.positions)}\n'
                f'Завершено: {state["closed_count"]}\n\n'
                + (f'Источников с ошибкой: {len(state.get("source_errors",{}))}\n\n' if state.get('source_errors') else '')
                +
                'Это отдельный учебный/исследовательский счёт. Его NET не входит в результат основного бота. '
                'Все исполнения здесь модельные. Новые стратегии не отправляют заявки.\n\n'
                'Для своего replay отправь файл *.strategy-replay.jsonl. '
                'По умолчанию только расчёт. Для виртуальных входов выбери стратегии и «Авто replay». '
                'Ручные рыночные входы остаются в основном меню; здесь подтверждение требует отдельного свежего источника.')
        rows = [[button('🧭 Все 15 стратегий','lab:catalog'),button('📈 Позиции','lab:positions')],
                [button(MODE[m],'lab:mode:'+m) for m in ('observe','manual','auto')],
                [button('🎛 Риск replay','lab:risk'),button('📤 Отчёт JSON','lab:export')]]
    rows.append([button('‹ Paper/replay','lab:home'),button('‹ Режимы бота','strategies')])
    return text[:3900], InlineKeyboardMarkup(inline_keyboard=rows)


async def import_jsonl(lab, content, allow_entries=True):
    import json
    from .strategy_math import Invalid
    if len(content.encode('utf-8')) > 2_000_000:
        raise Invalid('REPLAY_FILE_TOO_LARGE')
    records = [json.loads(line) for line in content.splitlines() if line.strip()]
    if len(records)>2000:
        raise Invalid('REPLAY_EVENT_LIMIT')
    if any(not isinstance(x,dict) or not {'id','type','ts'}<=x.keys() for x in records):
        raise Invalid('REPLAY_EVENT_OBJECT_REQUIRED')
    # Validate ordering before committing a prefix. Runtime failures still retain
    # the committed prefix; all event IDs make a retry idempotent.
    stamps = [D(x['ts']) for x in records]
    if stamps != sorted(stamps):
        raise Invalid('REPLAY_TIME_ORDER')
    results = []
    for event,stamp in zip(records,stamps):
        results.append(await lab.process(event,now=stamp,allow_entries=allow_entries))
    return results
