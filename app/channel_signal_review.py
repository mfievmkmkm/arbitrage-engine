"""Per-post quote-only assessment. A channel outcome is not execution evidence."""
import asyncio
import time
from html import escape
from .dex_signal_focus import parse_signal
from .dex_public_channel_reader import CHANNEL

REASONS = {
    'DEX_CEX_IDENTITY_UNVERIFIED': 'Не подтверждено, что контракт на CEX соответствует точному DEX-токену.',
    'PAIRED_PARTIAL_NET_LOW': 'Доход при частичном схождении не покрывает расходы и минимальную прибыль.',
    'PAIRED_REFERENCE_GAP_EXTREME': 'Аномальный разрыв выше 10%.',
    'PAIRED_IMMEDIATE_COST_HIGH': 'Немедленный выход слишком дорогой.',
    'DEX_VIRTUAL_ENTRY_UNAVAILABLE': 'Не удалось получить полный набор котировок и расходов.',
    'DEX_VIRTUAL_ENTRY_STALE': 'За время проверки котировки устарели.',
    'DEX_VIRTUAL_REVERSE_PREFLIGHT_NET_STOP': 'Обратный выход уже превышает допустимый убыток.',
}


class Reviewer:
    def __init__(self,bundle,clock=time.time):
        self.bundle,self.clock=bundle,clock

    async def __call__(self,post):
        prefix=('📨 <b>Проверка сигнала канала</b>\n'
                '<a href="https://t.me/'+CHANNEL+'/'+str(int(post['id']))+'">Исходное сообщение</a>\n')
        prefix+=('Пост UTC '+time.strftime('%Y-%m-%d %H:%M:%S',time.gmtime(post['timestamp']))+
                 ' · проверка '+time.strftime('%H:%M:%S',time.gmtime(self.clock()))+' UTC\n')
        if 'Aligned in' in post['text']:
            return prefix+'ℹ Отчёт канала о схождении. Это не новый вход и не подтверждение прибыли после комиссий.'
        if not 0<=self.clock()-post['timestamp']<=90:
            return prefix+'⛔ НЕ ВХОДИТЬ по этому сообщению: сигнал старше 90 секунд. Старые цены не используются.'
        try:
            s=parse_signal(post['text'])
        except ValueError as exc:
            return prefix+'⛔ ВХОД НЕ ПОДТВЕРЖДЁН: '+escape(str(exc))
        prefix+=escape(s['base'])+' · сеть '+escape(str(s['chain_id']))+'\nКонтракт: <code>'+escape(s['asset'])+'</code>\n'
        if s['side']=='buy':
            return prefix+('⛔ ВХОД НЕ ПОДТВЕРЖДЁН. Это LONG-сигнал канала. Для MEXC BUY → DEX SELL нужен тот же токен заранее на DEX либо проверенное заимствование; их нет в данных бота. '
                           'Односторонний LONG по DEX-ориентиру не фиксирует арбитраж.\n'
                           '<a href="https://www.mexc.com/futures/'+escape(s['base'],quote=True)+'_USDT">MEXC · фьючерс</a>')
        try:
            discovery=getattr(self.bundle,'route_discovery',None)
            if discovery is None:
                raise ValueError('Подбор точного DEX-маршрута недоступен')
            await asyncio.wait_for(discovery.signal(post['text'],post['timestamp']),20)
            from .dex_paired_experiment import PairedSource,NATIVE
            if s['chain_id'] not in NATIVE:
                raise ValueError('Нет полной парной модели с проверкой gas/выхода для этой сети')
            legacy=self.bundle.dex_experiments['dex_channel'].source
            routes=[r for r in legacy.routes if r['base']==s['base'] and r['chain_id']==s['chain_id']
                    and (r['asset']==s['asset'] if s['chain_id']=='solana' else r['asset'].lower()==s['asset'].lower())]
            if len(routes)!=1:
                raise ValueError('Нет однозначного маршрута для точного контракта')
            r=dict(routes[0],mode='paired',notional_usd=25,max_notional_usd=30,
                   native_symbol=NATIVE[s['chain_id']]+'/USDT:USDT',native_decimals=18)
            source=PairedSource(legacy,[r])
            result=await asyncio.wait_for(source.entry(r),35)
            if not result.get('ok'):
                reason=result.get('reason','Неизвестная причина')
                raise ValueError(REASONS.get(reason,reason))
            p=result['position'];pol=p['paired_policy']
            spread=pol['raw_edge']/p['entry_cash']*100
            if self.clock()-post['timestamp']>90:
                raise ValueError('Сигнал устарел за время проверки')
            if spread<5:
                raise ValueError(f'Спред свежей парной модели {spread:.3f}% меньше 5%')
            if p['reserve']>250:
                raise ValueError('Общего депозита $250 недостаточно на две стороны')
            return prefix+('🟡 <b>Допустим только PAPER-кандидат; LIVE-вход не подтверждён.</b>\n'
                f"DEX BUY ${p['entry_cash']:.2f} ↔ MEXC SHORT {p['base_qty']:.8g} BASE\n"
                f"DEX цена ≈{p['entry_cash']/p['base_qty']:.8g}; CEX sell ≈{p['entry_price']:.8g}\n"
                f"Спред {spread:.3f}% · модель затрат ${pol['costs']:.4f} · резерв funding ${pol['funding_reserve']:.4f}\n"
                f"При схождении половины разрыва NET модели ${pol['partial_net']:+.4f}; немедленный выход ${p['entry_immediate_net']:+.4f}\n"
                f"Модель: цель NET ${pol['target_usd']:.4f}; стоп ${pol['stop_usd']:.2f}; тайм-аут {pol['max_seconds']} сек.\n"
                'Доход зависит от будущего схождения. Проверка котировок не доказывает, что оно произойдёт. Балансы, реальные DEX-fills, gas и ликвидационный риск не подтверждены для LIVE.\n'
                '<a href="https://www.mexc.com/futures/'+escape(s['base'],quote=True)+'_USDT">MEXC · фьючерс</a>')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reason=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
            return prefix+'⛔ ВХОД НЕ ПОДТВЕРЖДЁН: '+escape(reason[:800])+'\nЦены канала не используются как исполнение. Ордеров бот не отправляет.'
