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
    scan=getattr(service,'scanner',None)
    duration=getattr(scan,'last_batch_seconds',None)
    health=getattr(scan,'provider_health',{}) if scan else {}
    provider=escape(health.get('provider', '0x'))
    text='🔎 <b>Сканер · Bybit ↔ DEX</b>\n\n'
    if scan:
        text+=f'Пар: {len(scan.catalog)} · потоков: {scan.workers}\n'
        text+=f'Попыток проверки: {scan.checked}\n'
        if health:
            text+=f'{provider} с запуска: запросов {health["requests_sent"]} · котировок {health["responses_ok"]}\n'
        if duration is not None:
            age=max(0,now-scan.last_batch_at)
            text+=f'Пакет: {duration:.2f} сек. · {age:.0f} сек. назад\n'
        if scan.last_sweep_seconds is not None:
            text+=f'Обход списка: {scan.last_sweep_seconds:.1f} сек.\n'
    else:
        text+='Первый цикл ещё не завершён.\n'
    text+='Топ по сценарному NET · не старше 30 сек.'
    rows=[]
    for i,c in enumerate(candidates[:3],1):
        p=c['position'];key=hashlib.sha256(json.dumps(route_key(c['route'])).encode()).hexdigest()[:16]
        text+=f'\n\n<b>{i}. {escape(p["symbol"])}</b>\nСценарный NET ${p["paired_policy"]["partial_net"]:+.3f}'
        if manual:
            rows.append([button(f'Проверить {p["symbol"]}','dcard:'+key,'primary')])
    reasons=getattr(scan,'last_reasons',{}) if scan else {}
    failures=sum(reasons.values())
    denied=sum(reasons.get(k,0) for k in ('DEX_HTTP_401','DEX_HTTP_403'))
    if health.get('blocked') and health.get('reason') in ('DEX_HTTP_401','DEX_HTTP_403'):
        seconds=max(0,health.get('retry_after_seconds',0)-(now-getattr(scan,'last_cycle_at',now)))
        text+=f'\n\n⛔ Доступ {provider} приостановлен · HTTP {health["reason"][-3:]}.\nПовтор проверки через {max(1,int(seconds))} сек.'
    elif failures and denied==failures:
        code='403' if reasons.get('DEX_HTTP_403') else '401'
        text+=f'\n\n⛔ {provider}: нет доступа к котировкам · HTTP {code}.\nНужна проверка API-ключа и разрешений. Поиск не подтверждён.'
    elif failures and reasons.get('DEX_HTTP_429',0)==failures:
        text+=f'\n\n⏳ {provider}: лимит запросов. Повтор после паузы провайдера.'
    elif not candidates and any(reasons.get(code) for code in ('DEX_VIRTUAL_TAX', 'DEX_VIRTUAL_TAX_INVALID')):
        text+='\n\nКотировки получены. Комиссии токенов не подтверждены — входов нет.'
    elif not candidates:
        text+='\n\nПодходящих проверенных пар сейчас нет.'
    if health.get('blocked') and health.get('reason') in ('DEX_HTTP_401','DEX_HTTP_403'):
        rows.append([button(f'Проверить доступ {provider}','pair:probe')])
    rows.append([button('↻ Обновить','pair:scan')])
    if manual:rows.append([button('Список пар','ctl:dex')])
    rows.append([button('‹ Назад','pair:show')])
    return text,InlineKeyboardMarkup(inline_keyboard=rows)
