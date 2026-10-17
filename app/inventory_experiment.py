"""Paper bootstrap, durable migration and the compact Telegram view."""
import json
import os
import time
from html import escape
from pathlib import Path
from aiogram.types import InlineKeyboardMarkup
from .tg_ui import button
from .inventory_paper import Engine, policy
from .inventory_public_source import Source, Service


def migrate(path,runtime,store,controls):
    marker=Path(str(path)+'.inventory_v1.json')
    if marker.exists():
        return
    # This replaces entry priorities, never monitoring or historical accounting.
    runtime.enabled.update(spot_inventory=True,futures_futures=False,dex_paired=False,
                           spot_spot=False,spot_futures=False,funding_arb=False)
    store.save(runtime.enabled)
    marker.parent.mkdir(parents=True,exist_ok=True)
    tmp=marker.with_suffix('.tmp')
    tmp.write_text(json.dumps(dict(version=1,ts=time.time(),paper_only=True,
                                  mechanism='SPOT_INVENTORY_REBALANCE',history_preserved=True)))
    tmp.replace(marker)


async def build(bundle,path,controls,runtime,notify,paused,venue_modes,network_clients=None):
    values={k:os.environ['INVENTORY_PAPER_'+k.upper()] for k in policy() if 'INVENTORY_PAPER_'+k.upper() in os.environ}
    venues=tuple(x.strip() for x in os.getenv('INVENTORY_PAPER_VENUES','bybit,gateio').split(','))
    registry=json.loads(os.environ['INVENTORY_PAPER_REGISTRY_JSON']) if os.getenv('INVENTORY_PAPER_REGISTRY_JSON') else None
    engine=Engine(path,values);await engine.init()
    source=Source(bundle.clients,venues,registry=registry,network_clients=network_clients)
    source.allowed_venue=lambda v:venue_modes(v)['paper'] and venue_modes(v)['scan']
    def allowed(symbol=None):
        return (not paused() and controls.environment=='paper' and controls.entry_reason('paper','spot_inventory',symbol) is None
                and runtime.enabled.get('spot_inventory',False))
    service=Service(source,engine,allowed);engine.on_event=notify
    service.rebalance_mode=lambda:'manual' if controls.profile('paper')['mode']=='manual' else 'auto'
    service.signal_allowed=lambda:not paused() and controls.environment=='paper' and runtime.enabled.get('spot_inventory',False)
    bundle.inventory_paper=engine;bundle.inventory_source=source
    bundle.runtime.add('spot_inventory',service)
    return service


REASONS={
    'INVENTORY_CANDIDATE':'Найден кандидат после проверки объёма и расходов.',
    'INVENTORY_EXECUTION_MODEL':'Покупка и продажа отсимулированы; результат записан.',
    'INVENTORY_MONITOR':'Состояние открытого цикла обновлено.',
    'INVENTORY_MANUAL_CLOSE':'Запрос ручного закрытия сохранён.',
    'INVENTORY_MANUAL_TRANSFER':'Запрошено восстановление запасов переводами.',
    'INVENTORY_MANUAL_FINISH':'Запрошено завершение цикла после зачисления переводов.',
    'INVENTORY_ENTRY_CARD_STALE':'Настройки изменились: нужна новая карточка входа.',

    'WAITING_FOR_SEED_QUOTES':'Ждём свежие стаканы для начального виртуального распределения.',
    'INVENTORY_SPREAD_TOO_SMALL':'Исполнимый спред меньше установленного минимума.',
    'INVENTORY_SPREAD_ANOMALY':'Разрыв выше допустимого: возможна аномалия или несовместимость.',
    'INVENTORY_NET_TOO_SMALL':'После комиссий, переводов и резерва прибыль недостаточна.',
    'INVENTORY_NETWORK_STATUS_UNKNOWN':'Нет свежих подтверждённых данных о вводе и выводе.',
    'INVENTORY_NETWORK_IDENTITY_OR_FEE_UNKNOWN':'Не подтверждены сеть, контракт, комиссия или минимум перевода.',
    'INVENTORY_NETWORK_UNAVAILABLE':'Ввод или вывод на нужной сети остановлен.',
    'INVENTORY_REBALANCE_OR_RECOVERY_PENDING':'Сначала завершаем восстановление предыдущего цикла.',
    'INVENTORY_PORTFOLIO_MARK':'Портфель пересчитан по свежим ценам.',
    'INVENTORY_QUOTE_STALE':'Стакан устарел; вход пропущен.',
    'INVENTORY_QUOTES_NOT_SYNCHRONIZED':'Данные двух бирж получены с слишком большой разницей во времени.',
    'INVENTORY_TRANSFER_MINIMUM':'Размер сделки меньше минимума перевода.',
    'INVENTORY_VALUATION_UNAVAILABLE':'Не хватает свежих цен для оценки всего портфеля.',
    'INVENTORY_DAILY_STOP':'Достигнут дневной лимит убытка завершённых циклов.',
    'INVENTORY_PORTFOLIO_STOP':'Достигнут лимит потери стоимости всего портфеля, включая запас монет.',
    'INVENTORY_UNHEDGED_TIMEOUT':'Непокрытый остаток не удалось закрыть за 10 секунд; аварийное сопровождение продолжается.',
    'INVENTORY_TRANSFER_OR_RECOVERY_TIMEOUT':'Превышен срок перевода или аварийного закрытия.',
    'INVENTORY_CAPTURE_UNAVAILABLE':'Не удалось получить подтверждённые рыночные данные.',
    'INVENTORY_NATIVE_MINIMUM_OR_FEE_UNKNOWN':'Биржа не вернула минимумы ордера или комиссию.',
    'INVENTORY_SPOT_MARKET_UNAVAILABLE':'Нужная спотовая пара недоступна.',
    'INVENTORY_PUBLIC_NETWORK_DATA_EMPTY':'Нет данных сетей; может требоваться ключ с доступом на чтение.',
    'INVENTORY_NOTIFICATION_DELIVERED':'Объяснение сделки отправлено.',
    'INVENTORY_DEPTH_LOW':'В стакане недостаточно объёма для обеих сторон.',
}


def human(reason):
    return REASONS.get(reason,'Проверка не пройдена; подробная причина есть в выгрузке.')


def render(engine,source=None):
    if engine is None:
        return '📦 Spot/Spot · PAPER\nЭксперимент ещё не готов.'
    from .inventory_cards import STATUS
    s=engine.state;p=engine.p;v=s.get('valuation',{})
    lines=['📦 <b>Spot/Spot · размещённые средства</b>',
        f'Отдельный PAPER-счёт ${p["capital"]:.2f}\nСпред {p["min_spread_pct"]:g}–{p["max_spread_pct"]:g}% · покупка до ${p["max_notional"]:g}\n'
        f'NET после расходов и резерва от ${p["min_net"]:g}',
        f'Завершено {s["closed"]} · плюс {s["wins"]} / минус {s["losses"]}\n'
        f'NET циклов модели: <b>{float(s["realized"]):+.4f} USDT</b>\nОткрытых циклов {len(s["positions"])}\n'
        f'Комиссии {float(s["fees"]):.4f} · переводы {float(s["transfer_costs"]):.4f} USDT\n'
        f'Просадка завершённых циклов ${float(s.get("drawdown_realized",0)):.4f}']
    if v:
        lines.append(f'Стоимость счёта ${float(v["equity"]):.4f}\nПолный результат {float(v["total_pnl"]):+.4f}\n'
                     f'Рыночное движение исходного запаса {float(v["inventory_market_pnl"]):+.4f}\n'
                     f'Относительно удержания запасов {float(v["excess_vs_inventory"]):+.4f} USDT\n'
                     f'Оценка UTC {time.strftime("%H:%M:%S",time.gmtime(float(v["ts"])))}')
    if s.get('valuation_error') or (v and time.time()-float(v['ts'])>15):
        lines.append('⚠ Свежая полная оценка портфеля недоступна; предыдущая оценка устарела.')
    if s.get('halted'):
        lines.append('⛔ Новые входы остановлены: '+escape(human(s['halted'])))
    for pos in list(s['positions'].values())[:2]:
        x=pos['plan']
        lines.append(f'<b>{escape(x["asset"])}</b> · {escape(x["buy"])} → {escape(x["sell"])}\n'
            f'{escape(STATUS.get(pos['status'],pos['status']))} · спред входа {float(x["spread_pct"]):.3f}%\n'
            f'Непокрытый остаток BASE: {escape(pos["residual"])}\n'
            + '\n'.join(escape(t['asset'])+': '+escape(t['status']) for t in pos['transfers']))
    lines.append('Последняя проверка: '+escape(human(s['last_reason'])))
    errors=source.errors if source else {}
    if errors:
        lines.append('Данные: '+'; '.join(escape(k)+': '+escape(human(str(x))) for k,x in list(errors.items())[:3]))
    lines.append('Прибыль возникает при исполнении разницы цен, сжатия не ждём. Остаток одной ноги закрывается по рынку. '
        'NET цикла признаётся после восстановления запасов. Запас монет может дешеветь. '
        'Срок перевода 15 минут — модельное допущение; fills и quote-комиссии не сертифицируют LIVE. '
        'Счёт не суммируется с прежним Paper и DEX. Все деньги виртуальные.')
    return '\n\n'.join(lines)[:3900]


def keyboard(engine=None):
    positions=[]
    if engine:
        positions=[[button('📈 '+p['plan']['asset']+' · управление #'+str(p['id']),'inv:pos:'+str(p['id']))] for p in engine.positions.values()]
    return InlineKeyboardMarkup(inline_keyboard=positions+[
        [button('Спред ≥5%','inv:set:min_spread_pct:5'),button('≥7.5%','inv:set:min_spread_pct:7.5')],
        [button('Покупка $10','inv:set:max_notional:10'),button('$25','inv:set:max_notional:25'),button('$50','inv:set:max_notional:50')],
        [button('NET ≥$0.25','inv:set:min_net:0.25'),button('≥$0.50','inv:set:min_net:0.5')],
        [button('Включить/выключить входы','strategy:spot_inventory')],
        [button('🎛 Среда и режим','ctl:show'),button('Ручной вход','inv:card')],
        [button('🧪 Проверить симулятор','inv:demo'),button('Прежние позиции','paper')],
        [button('↻ Обновить','inv:show'),button('📒 Выгрузка','export')],
        [button('‹ Главное меню','home')]])
