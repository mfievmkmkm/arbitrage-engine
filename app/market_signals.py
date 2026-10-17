"""Broad read-only spot discovery. Tickers discover; fresh depth prices signals.

No API keys, wallet access, trading, or PAPER-wallet expansion. Every available
CCXT centralized spot adapter is attempted in bounded batches. Failed venues
remain visible and retry with backoff; derivatives/DEX adapters are excluded.
"""
import asyncio
import math
import os
import time
from html import escape
import ccxt.async_support as ccxt
import aiosqlite
from .inventory_paper import fill
from .inventory_public_source import step, Source
from .inventory_network_metadata import enrich
from .strategy_math import D, ONE, ZERO, common_quantity, plain
from .public_books import normalize
from .market_signal_links import market_link
from .market_signal_store import Store

PRIORITY = ('bybit','gate','gateio','mexc','binance','okx','bitget','kucoin','htx','coinex','bitmart')


def positive(value):
    try:
        n = float(value)
        return n if math.isfinite(n) and n > 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def spot(m):
    return (m.get('spot') is True and m.get('active') is True and m.get('quote') == 'USDT'
            and isinstance(m.get('base'),str) and 1<=len(m['base'])<=32 and m['base'].isalnum()
            and not m.get('contract') and not m.get('leveraged'))


def discover():
    found = []
    for name in dict.fromkeys((*PRIORITY, *ccxt.exchanges)):
        if name not in ccxt.exchanges:
            continue
        client = getattr(ccxt, name)({'enableRateLimit': True, 'timeout': 10000,
                                     'options': {'defaultType': 'spot'}})
        if (client.has.get('spot') is True and client.has.get('fetchOrderBook') in (True,'emulated')
                and (client.has.get('fetchTickers') in (True,'emulated') or client.has.get('fetchTicker') in (True,'emulated'))
                and client.describe().get('dex') is not True):
            found.append(name)
    return found


def priced(book, client, m):
    return dict(asks=book['asks'], bids=book['bids'], ts=D(book['timestamp'])/1000,
                step=step(client,m), fee_rate=max(D(str(m['taker'])),D('.002')))


def economics(a, b, budget=25, capital=250):
    budget, capital = D(budget), D(capital)
    if budget <= 0 or capital <= 0 or budget > capital:
        raise ValueError('Некорректный бюджет')
    qty = common_quantity(budget/(D(a['asks'][0][0])*(ONE+a['fee_rate'])),[a['step'],b['step']])
    if qty <= 0:
        raise ValueError('Размер меньше шага ордера')
    qa, buy, fa = fill(a,qty,'buy')
    qb, sell, fb = fill(b,qty,'sell')
    if qa != qty or qb != qty:
        raise ValueError('Недостаточная глубина на заданный объём')
    # Includes capital occupied by BASE already held on the sell venue.
    required = buy+fa+sell
    if required > capital:
        raise ValueError('На две стороны не хватает общего депозита')
    return plain(dict(qty=qty,buy_cash=buy,sell_cash=sell,buy_vwap=buy/qty,sell_vwap=sell/qty,
                      fees=fa+fb,trade_net=sell-buy-fa-fb,spread_pct=(sell/buy-ONE)*100,
                      capital_required=required))


class Scanner:
    interval = 15

    def __init__(self, path, notify, clock=time.time, venues=None):
        self.clock, self.notify = clock, notify
        self.store = Store(path,clock)
        self.names = venues if venues is not None else discover()
        self.clients, self.health, self.tickers, self.currency_cache = {}, {}, {}, {}
        self.retry, self.load_cursor = {}, 0
        self.symbol_cursor = {}
        self.loader = None
        self.workers = {}
        self.route_cursor = 0
        self.gate = asyncio.Semaphore(4)
        self.entry_enabled = True
        self.channel_review = None
        self.last_scan = 0
        self.lock = asyncio.Lock()
        self.budget = min(100, max(5, float(os.getenv('MARKET_SIGNALS_NOTIONAL','25'))))
        self.capital = 250

    async def init(self):
        await self.store.init()

    async def load(self, name):
        if name in self.clients or self.clock() < self.retry.get(name,0):
            return
        client = getattr(ccxt,name)({'enableRateLimit':True,'timeout':10000,'options':{'defaultType':'spot'}})
        try:
            async with self.gate:
                await asyncio.wait_for(client.load_markets(),20)
            if not any(spot(m) for m in client.markets.values()):
                raise ValueError('Нет активного USDT-спота')
            self.clients[name] = client
            self.health[name] = 'Загружена; ждём котировки'
            self.workers[name] = asyncio.create_task(self.ticker_loop(name))
        except asyncio.CancelledError:
            await client.close()
            raise
        except Exception as exc:
            self.health[name] = type(exc).__name__ if not isinstance(exc,ValueError) else str(exc)
            self.retry[name] = self.clock()+900
            await client.close()

    async def load_loop(self):
        while True:
            batch=[self.names[(self.load_cursor+i)%len(self.names)] for i in range(min(4,len(self.names)))] if self.names else []
            self.load_cursor+=len(batch)
            await asyncio.gather(*(self.load(v) for v in batch))
            await asyncio.sleep(2)

    async def ticker_loop(self,name):
        while True:
            await self.tick(name)
            await asyncio.sleep(45)

    async def tick(self, name):
        if self.clock() < self.retry.get(name,0):
            return
        try:
            async with self.gate:
                c=self.clients[name]
                if c.has.get('fetchTickers') in (True,'emulated'):
                    rows = await asyncio.wait_for(c.fetch_tickers(),12)
                else:
                    symbols=[s for s,m in c.markets.items() if spot(m)]
                    count=len(symbols)
                    start=self.symbol_cursor.get(name,0)
                    symbols=(symbols[start:]+symbols[:start])[:4]
                    self.symbol_cursor[name]=(start+len(symbols))%max(1,count)
                    rows={s:await asyncio.wait_for(c.fetch_ticker(s),5) for s in symbols}
            self.tickers[name] = (self.clock(),{s:t for s,t in rows.items() if spot(self.clients[name].markets.get(s,{}))
                                                    and positive(t.get('bid')) and positive(t.get('ask'))
                                                    and t['bid'] < t['ask']})
            self.health[name] = 'OK'
        except Exception as exc:
            self.tickers.pop(name,None)
            self.health[name] = type(exc).__name__
            self.retry[name] = self.clock()+120

    def candidates(self):
        symbols = {}
        for venue,(ts,rows) in self.tickers.items():
            if not 0 <= self.clock()-ts <= 90:
                continue
            for symbol,t in rows.items():
                symbols.setdefault(symbol,[]).append((venue,t))
        out = []
        for symbol,rows in symbols.items():
            buys = sorted(rows,key=lambda x:x[1]['ask'])[:2]
            sells = sorted(rows,key=lambda x:-x[1]['bid'])[:2]
            for a,ta in buys:
                for b,tb in sells:
                    gap=(tb['bid']/ta['ask']-1)*100
                    if a!=b and math.isfinite(gap) and gap>=5:
                        out.append((gap,symbol,a,b))
        return sorted(out,reverse=True)

    async def book(self,venue,symbol):
        c=self.clients[venue]; m=c.markets[symbol]
        if m.get('taker') is None or not 0 <= D(m['taker']) < ONE:
            raise ValueError('Неизвестна торговая комиссия')
        async with self.gate:
            start=self.clock(); raw=await asyncio.wait_for(c.fetch_order_book(symbol),8)
            book=normalize(raw,symbol,start,self.clock(),3)
        return priced(book,c,m)

    async def currencies(self,venue):
        old=self.currency_cache.get(venue)
        if old and 0<=self.clock()-old[0]<=240:
            return old[1]
        c=self.clients[venue]
        if c.has.get('fetchCurrencies') is not True:
            raise ValueError('Биржа не предоставляет статус сетей публично')
        async with self.gate:
            rows=await asyncio.wait_for(c.fetch_currencies(),10)
        if not isinstance(rows,dict) or not rows:
            raise ValueError('Нет публичных данных сетей')
        rows=enrich(rows,None)
        self.currency_cache[venue]=(self.clock(),rows)
        return rows

    async def route(self,base,a,b):
        # Exact shared chain + contract, with curated native exceptions only.
        ca,cb=await asyncio.gather(self.currencies(a),self.currencies(b))
        from .inventory_public_source import NATIVE, same_contract
        if base in NATIVE:
            registry={base:dict(native=True,networks=list(NATIVE[base]))}
        else:
            an=Source.networks_for(ca.get(base,{}));bn=Source.networks_for(cb.get(base,{}))
            matches=[(n,x.get('contract')) for n,x in an.items() if n in bn and x.get('contract')
                     and same_contract(x['contract'],bn[n].get('contract'),n)]
            if not matches:
                raise ValueError('Не подтверждён одинаковый токен: сеть и контракт')
            n,contract=matches[0]
            registry={base:dict(networks=[n],contract=contract)}
        src=Source(self.clients,(a,b),registry=registry,clock=self.clock)
        src.networks={a:ca,b:cb};src.network_ts={a:self.currency_cache[a][0],b:self.currency_cache[b][0]}
        try:
            return src.network(base,a,b),src.network('USDT',b,a)
        except ValueError:
            raise ValueError('Не подтверждены сеть, комиссия или минимумы перевода BASE/USDT') from None

    async def evaluate(self,symbol,a,b):
        base=symbol.split('/')[0]
        # Metadata requests happen BEFORE fresh books.
        reasons=[];routes=None
        try:
            routes=await self.route(base,a,b)
        except Exception as exc:
            reasons.append(str(exc) if isinstance(exc,ValueError) else type(exc).__name__)
        aa,bb=await asyncio.gather(self.book(a,symbol),self.book(b,symbol))
        if abs(aa['ts']-bb['ts'])>1 or any(not ZERO<=D(self.clock())-x['ts']<=3 for x in (aa,bb)):
            raise ValueError('Стаканы устарели или не синхронизированы')
        e=economics(aa,bb,self.budget,self.capital)
        if D(e['spread_pct'])<5:
            return None
        if D(e['spread_pct'])>10:
            reasons.append('Спред выше 10%: аномалия, требуется отдельная проверка')
        for v,cash in ((a,e['buy_cash']),(b,e['sell_cash'])):
            m=self.clients[v].markets[symbol];limits=m.get('limits') or {}
            minimum=(limits.get('amount') or {}).get('min'); cost=(limits.get('cost') or {}).get('min')
            maximum=(limits.get('amount') or {}).get('max'); max_cost=(limits.get('cost') or {}).get('max')
            if minimum is None or cost is None:
                reasons.append(v+': неизвестен минимум ордера')
            elif D(e['qty'])<D(minimum) or D(cash)<D(cost):
                reasons.append(v+': размер меньше минимума ордера')
            if (maximum is not None and D(e['qty'])>D(maximum)) or (max_cost is not None and D(cash)>D(max_cost)):
                reasons.append(v+': превышен максимум ордера')
        if routes:
            rb,rq=routes
            transfer=D(rb['fee'])*D(e['buy_vwap'])+D(rq['fee'])
            if D(e['qty'])<=D(rb['fee']) or D(e['qty'])<D(rb['minimum']) or D(e['qty'])-D(rb['fee'])<D(rb['minimum']):
                reasons.append('BASE не хватает на комиссию и минимумы перевода')
            if D(e['sell_cash'])-D(bb['fee_rate'])*D(e['sell_cash'])-D(rq['fee'])<D(rq['minimum']):
                reasons.append('USDT не хватает на обратный перевод')
            e.update(transfer_cost=str(transfer),net=str(D(e['trade_net'])-transfer-D('.10')),
                     network=rb['network'],quote_network=rq['network'])
            if D(e['net'])<D('.25'):
                reasons.append('NET после расходов и резерва меньше $0.25')
        else:
            e.update(transfer_cost=None,net=None)
        e.update(symbol=symbol,buy=a,sell=b,ts=self.clock(),reasons=reasons)
        return e

    def format(self,e):
        lines=['📡 <b>SPOT · сигнал для ручной проверки</b>',escape(e['symbol'])+' · '+escape(e['buy'])+' → '+escape(e['sell']),
               f"Спред по глубине {float(e['spread_pct']):.3f}% · BASE {escape(e['qty'])}",
               f"Купить: VWAP {float(e['buy_vwap']):.8g} · ${float(e['buy_cash']):.4f}",
               f"Продать: VWAP {float(e['sell_vwap']):.8g} · ${float(e['sell_cash']):.4f}",
               f"Торговые комиссии ≥${float(e['fees']):.4f} (модель ≥0.2%/сторону)",
               f"Разница после торговых комиссий: ${float(e['trade_net']):+.4f}"]
        if e['net'] is not None:
            lines.append(f"Переводы ≈${float(e['transfer_cost']):.4f} · резерв $0.10\nNET модели: ${float(e['net']):+.4f}\nСети: {escape(e['network'])} / USDT {escape(e['quote_network'])}")
        else:
            lines.append('Итоговый NET неизвестен: стоимость/возможность переводов не подтверждена.')
        if e['reasons']:
            lines.append('⛔ ВХОД НЕ ПОДТВЕРЖДЁН:\n'+'\n'.join('• '+escape(r) for r in e['reasons']))
        else:
            lines.append('🟡 Экономика модели прошла фильтры. Условие: USDT уже на покупке и такой же BASE уже на продаже. Балансы и ваши комиссии не проверены.')
        lines.append(f"Занятый капитал двух сторон ≈${float(e['capital_required']):.2f} из общих $250. Это не размер каждой ноги.")
        for label,v in (('Купить',e['buy']),('Продать',e['sell'])):
            url,exact=market_link(v,e['symbol'].split('/')[0],self.clients[v].urls.get('www'))
            if url:
                lines.append('<a href="'+escape(url,quote=True)+'">'+label+(' · пара' if exact else ' · сайт биржи')+'</a>')
        lines.append('Котировки UTC '+time.strftime('%H:%M:%S',time.gmtime(e['ts']))+'. До клика могут измениться. Покупка → перевод → продажа НЕ фиксирует этот спред: цена за время перевода неизвестна. Неизвестная комиссия депозита в модели не учтена. Ордеров и переводов бот не отправляет.')
        return '\n'.join(lines)

    async def queue_channel(self,post):
        await self.store.enqueue('channel:'+str(post['id']),'channel',post)

    async def flush(self):
        for item in await self.store.pending():
            if item['kind']=='spot' and self.clock()-item['ts']>30:
                await self.store.delivered(item['key']);continue
            result=item['result']
            if result is None:
                if item['kind']=='channel':
                    if self.channel_review is None:
                        continue
                    result=await self.channel_review(item['payload'])
                else:
                    result=self.format(item['payload'])
                await self.store.result(item['key'],result)
            # Ambiguous Telegram timeout may duplicate a message, never a trade.
            delivery=result
            if item['kind']=='channel' and self.clock()-item['ts']>90:
                delivery='⚠ Задержанная доставка. Проверка ниже устарела; НЕ использовать для текущего входа.\n\n'+delivery
            await self.notify(delivery)
            await self.store.delivered(item['key'])

    async def cycle(self):
        async with self.lock:
            if not await self.store.enabled():
                return []
            if self.loader is None and self.names:
                self.loader=asyncio.create_task(self.load_loop())
            await self.flush()
            # Bounded book work; failed top routes don't starve others forever.
            candidates=self.candidates()
            if candidates:
                offset=self.route_cursor%len(candidates)
                candidates=candidates[offset:]+candidates[:offset]
            bucket=int(self.clock()//300)
            for _,symbol,a,b in candidates:
                self.route_cursor+=1
                key=f'spot:{symbol}:{a}:{b}:{bucket}'
                async with aiosqlite.connect(self.store.path) as db:
                    exists=await (await db.execute('SELECT 1 FROM market_signal_queue WHERE key=?',(key,))).fetchone()
                if exists:
                    continue
                try:
                    e=await self.evaluate(symbol,a,b)
                except Exception as exc:
                    self.health['route:'+a+':'+b]=type(exc).__name__+': '+str(exc)[:100]
                    e=None
                # Persist misses too, so one unsupported route cannot starve
                # the same lower-ranked candidates on every cycle.
                if e:
                    await self.store.enqueue(key,'spot',e)
                else:
                    await self.store.enqueue(key,'miss',{})
                    await self.store.delivered(key)
                await self.flush()
                break
            self.last_scan=self.clock()
        return []

    def status(self):
        ok=[v for v in self.clients if self.health.get(v)=='OK' and v in self.tickers and 0<=self.clock()-self.tickers[v][0]<=90]
        failed=[v+': '+s for v,s in self.health.items() if v not in ok and not v.startswith('route:')]
        return ('📡 <b>Сигналы · только чтение</b>\n'
                f'Общий депозит $250 · расчёт покупки до ${self.budget:g} · минимум 5%\n'
                f'Обнаружено адаптеров {len(self.names)} · загружено {len(self.clients)} · свежие тикеры {len(ok)}\n'
                'Котировки: '+escape(', '.join(ok) or 'ожидаем')+'\n'
                'Ожидают/недоступны: '+escape('; '.join(failed)[:1200] or 'нет')+'\n'
                f'Ещё не проверено: {len(set(self.names)-set(self.health))}\n'
                'Покрытие: активный USDT-спот CEX, публичные API CCXT с тикерами и стаканами. При отсутствии массового API пары перебираются постепенно. DEX и фьючерсы не смешиваются со спотом. Загрузка с ограничением запросов; охват не означает доступность биржи вашему аккаунту.\n'
                'Сигналы не открывают сделки. PAPER-счёт и его список бирж сохраняются отдельно.')

    async def close(self):
        tasks=([self.loader] if self.loader else [])+list(self.workers.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        await asyncio.gather(*(c.close() for c in self.clients.values()),return_exceptions=True)
