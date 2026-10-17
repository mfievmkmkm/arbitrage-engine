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
            rows.append([button('🔎 Сканер','pair:scan','primary')])
        elif active:
            rows.append([button('🔎 Сканер','pair:scan','primary')])
    rows += [[button('🛡 Риск','ctl:risk'),button('📈 Сделки','paper')],
             [button('‹ Назад','strategies')]]
    return text,InlineKeyboardMarkup(inline_keyboard=rows)


def scanner(engine,service,manual=True):
    import hashlib,json
    from html import escape
    from .dex_virtual_paper_source import route_key
    now=engine.clock()
    candidates=[c for c in getattr(service,'candidates',{}).values()
                if c['position'].get('cex_venue')=='bybit'
                and c['position'].get('fee_source')=='ACCOUNT_READ_ONLY'
                and 0<=now-c['position']['opened_at']<=30]
    candidates.sort(key=lambda c:c['position']['paired_policy']['partial_net'],reverse=True)
    duration=getattr(service,'last_cycle_seconds',None)
    text='🔎 <b>Сканер · Bybit ↔ DEX</b>\n\n'
    text+=f'Последний цикл: {duration:.2f} сек.\n' if duration is not None else 'Первый цикл ещё не завершён.\n'
    text+='Пауза между циклами: 5 сек.\n'
    text+='Топ проверенных кандидатов · оценки до 30 сек.'
    rows=[]
    for i,c in enumerate(candidates[:3],1):
        p=c['position'];key=hashlib.sha256(json.dumps(route_key(c['route'])).encode()).hexdigest()[:16]
        text+=f'\n\n<b>{i}. {escape(p["symbol"])}</b>\nСценарный NET ${p["paired_policy"]["partial_net"]:+.3f}'
        if manual:
            rows.append([button(f'Проверить {p["symbol"]}','dcard:'+key,'primary')])
    if not candidates:text+='\n\nПодходящих проверенных пар сейчас нет.'
    rows.append([button('↻ Обновить','pair:scan')])
    if manual:rows.append([button('Список пар','ctl:dex')])
    rows.append([button('‹ Назад','pair:show')])
    return text,InlineKeyboardMarkup(inline_keyboard=rows)
