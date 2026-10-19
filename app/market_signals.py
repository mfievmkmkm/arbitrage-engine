"""Broad read-only spot discovery. Tickers discover; fresh depth prices signals.

No API keys, wallet access, trading, or PAPER-wallet expansion. Every available
CCXT centralized spot adapter is attempted in bounded batches. Failed venues
remain visible and retry with backoff; derivatives/DEX adapters are excluded.
"""
import asyncio
import math
import os
import time
import hashlib
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
from .transfer_signal_math import plan as transfer_plan
from .transfer_signal_paper import Paper as TransferPaper

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
                step=step(client,m), fee_rate=D(str(m['taker'])))


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

    def __init__(self, path, notify, clock=time.time, venues=None,trade_venues=(),account_clients=None,shared_clients=None,paper_venues=()):
        self.clock, self.notify = clock, notify
        self.store = Store(path,clock)
        self.trade_venues = set(trade_venues)
        self.paper_venues = self.trade_venues | set(paper_venues)
        self.shared_clients=dict(shared_clients or {})
        self.names = list(dict.fromkeys((*sorted(self.paper_venues),*self.shared_clients,
                                         *(venues if venues is not None else discover()))))
        self.clients, self.health, self.tickers, self.currency_cache = dict(self.shared_clients), {}, {}, {}
        self.pool_lock=asyncio.Lock();self.max_active_venues=1
        self.loaded_order=[]
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
        from .signal_account_check import Accounts
        self.accounts=Accounts(account_clients or {},clock)
        self.account_fee_cache={}
        self.paper=TransferPaper(self.store,clock)
        self.position_card=None
        self.actionable_only=False
        self.remove_position_card=None

    async def init(self):
        await self.store.init()
        await self.paper.init()
        for name in self.shared_clients:
            self.health[name]='Загружена; ждём котировки'
            self.workers[name]=asyncio.create_task(self.ticker_loop(name))

    async def load(self, name):
        async with self.pool_lock:
            if name in self.clients or self.clock() < self.retry.get(name,0):return
            client = getattr(ccxt,name)({'enableRateLimit':True,'timeout':10000,'options':{'defaultType':'spot'}})
            try:
                async with self.gate:
                    await asyncio.wait_for(client.load_markets(),20)
                if not any(spot(m) for m in client.markets.values()):
                    raise ValueError('Нет активного USDT-спота')
                if len(self.loaded_order)>=self.max_active_venues:
                    evict=next((v for v in self.loaded_order if v not in self.paper_venues),None)
                    if evict is None:
                        self.health[name]='Нет свободного слота для дополнительной биржи'
                        await client.close()
                        return
                    worker=self.workers.pop(evict,None)
                    if worker:
                        worker.cancel();await asyncio.gather(worker,return_exceptions=True)
                    previous=self.clients.pop(evict)
                    self.tickers.pop(evict,None);self.currency_cache.pop(evict,None)
                    self.loaded_order.remove(evict);self.health[evict]='Ротация: не активна'
                    await previous.close()
                self.clients[name] = client;self.loaded_order.append(name)
                self.health[name] = 'Загружена; ждём котировки'
                self.workers[name] = asyncio.create_task(self.ticker_loop(name))
                if name=='mexc':
                    import logging
                    logging.getLogger('arbitrage').info('MEXC_SPOT_PAPER | markets=%s | public_only=%s',
                                                        sum(spot(m) for m in client.markets.values()),name not in self.trade_venues)
            except asyncio.CancelledError:
                await client.close();raise
            except Exception as exc:
                self.health[name] = type(exc).__name__ if not isinstance(exc,ValueError) else str(exc)
                self.retry[name] = self.clock()+900
                if name=='mexc':
                    import logging
                    logging.getLogger('arbitrage').warning('MEXC_SPOT_PAPER | load_failed=%s',
                                                          type(exc).__name__)
                await client.close()

    async def load_loop(self):
        while True:
            if self.names:
                name=self.names[self.load_cursor%len(self.names)]
                self.load_cursor+=1
                await self.load(name)
            await asyncio.sleep(20)

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

    def candidates(self,min_spread=5):
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
                    if a!=b and math.isfinite(gap) and gap>=min_spread:
                        out.append((gap,symbol,a,b))
        return sorted(out,reverse=True)

    async def book(self,venue,symbol):
        c=self.clients[venue]; m=c.markets[symbol]
        if m.get('taker') is None or not 0 <= D(m['taker']) < ONE:
            raise ValueError('Неизвестна торговая комиссия')
        account=self.accounts.client(venue)
        if venue in self.trade_venues and account is not None:
            cached=self.account_fee_cache.get((venue,symbol))
            if not cached or not 0<=self.clock()-cached[0]<30:
                if account.has.get('fetchTradingFee') is not True:
                    raise ValueError('Нет API чтения персональной spot-комиссии')
                fee=await asyncio.wait_for(account.fetch_trading_fee(symbol),8)
                if fee.get('symbol')!=symbol or fee.get('taker') is None or not ZERO<=D(fee['taker'])<ONE:
                    raise ValueError('Персональная spot-комиссия не подтверждена')
                cached=(self.clock(),D(fee['taker']))
                self.account_fee_cache[(venue,symbol)]=cached
            fee_rate,evidence=cached[1],'ACCOUNT_READ_ONLY'
        else:
            fee_rate,evidence=D(m['taker']),'PUBLIC_MARKET_RATE_PAPER_ASSUMPTION'
        async with self.gate:
            start=self.clock(); raw=await asyncio.wait_for(c.fetch_order_book(symbol),8)
            book=normalize(raw,symbol,start,self.clock(),3)
        result=priced(book,c,m)
        result['fee_rate']=fee_rate
        result['fee_evidence']=evidence
        return result

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

    async def quote_route(self,src,dst):
        ca,cb=await asyncio.gather(self.currencies(src),self.currencies(dst))
        from .inventory_public_source import NATIVE
        source=Source(self.clients,(src,dst),registry={'BTC':dict(native=True,networks=list(NATIVE['BTC']))},clock=self.clock)
        source.networks={src:ca,dst:cb};source.network_ts={src:self.currency_cache[src][0],dst:self.currency_cache[dst][0]}
        return source.network('USDT',src,dst)

    async def evaluate(self,symbol,a,b):
        base=symbol.split('/')[0]
        # Metadata requests happen BEFORE fresh books.
        reasons=[];routes=None
        control=await self.store.control()
        try:
            routes=await self.route(base,a,b)
        except Exception as exc:
            reasons.append(str(exc) if isinstance(exc,ValueError) else type(exc).__name__)
        configured=a in self.paper_venues and b in self.paper_venues
        funding=None;source=None
        if configured:
            source=self.paper.funding_source(a,D(control['budget']))
            if source is None:
                reasons.append('PAPER: выбери стартовую биржу и обеспечь виртуальный USDT')
            elif source!=a:
                try:
                    funding=await self.quote_route(source,a)
                except Exception as exc:
                    reasons.append('USDT до биржи покупки: '+(str(exc) if isinstance(exc,ValueError) else type(exc).__name__))
        aa,bb=await asyncio.gather(self.book(a,symbol),self.book(b,symbol))
        if abs(aa['ts']-bb['ts'])>1 or any(not ZERO<=D(self.clock())-x['ts']<=3 for x in (aa,bb)):
            raise ValueError('Стаканы устарели или не синхронизированы')
        raw_spread=(D(bb['bids'][0][0])/D(aa['asks'][0][0])-ONE)*100
        if raw_spread<D(control['min_spread']):
            return None
        transfer=None
        if routes:
            try:
                transfer=transfer_plan(aa,bb,*routes,budget=control['budget'],capital=self.capital,
                    min_spread=control['min_spread'],max_spread=control['max_spread'],
                    min_net=control['min_net'],stress_drop=control['stress_drop'])
                reasons.extend(transfer['reasons'])
            except (ValueError,KeyError) as exc:
                reasons.append('Последовательный перевод: '+str(exc))
        if transfer:
            for venue,qty,cash in ((a,transfer['buy_qty'],transfer['buy_cash']),
                                   (b,transfer['sell_qty'],transfer['sell_cash'])):
                try:
                    from .transfer_signal_math import limits
                    limits(self.clients[venue].markets[symbol],qty,cash)
                except ValueError as exc:
                    reasons.append(venue+': '+str(exc))
        if transfer and configured and source and source!=a and funding:
            try:
                    fee=D(funding['fee']);amount=D(control['budget'])+fee
                    if funding.get('deposit_fee') is None or D(funding['deposit_fee'])!=0 or funding.get('deposit_fee_percentage') is not False:
                        raise ValueError('Не подтверждена нулевая комиссия ввода USDT')
                    if amount<D(funding['withdraw_minimum']) or D(control['budget'])<D(funding['deposit_minimum']):
                        raise ValueError('Предварительный USDT-перевод меньше минимума')
                    if D(self.paper.state['balances'][source])<amount:raise ValueError('Недостаточно USDT на бирже-источнике')
                    transfer['funding_source']=source;transfer['funding_route']=funding
                    transfer['funding_amount']=str(amount)
                    transfer['funding_credit']=str(D(control['budget']))
                    transfer['net_after_reserve']=str(D(transfer['net_after_reserve'])-fee)
                    transfer['stress_net']=str(D(transfer['stress_net'])-fee)
                    if D(transfer['stress_net'])<D(control['min_net']):
                        reasons.append('Предварительный USDT-перевод уничтожает минимальный NET')
            except ValueError as exc:
                reasons.append('USDT до биржи покупки: '+str(exc))
        e=dict(symbol=symbol,buy=a,sell=b,ts=float(min(aa['ts'],bb['ts'])),
               spread_pct=str(raw_spread),reasons=list(dict.fromkeys(reasons)),
                 transfer=transfer,mode=control['mode'],trade_configured=configured,
                 fee_evidence={a:aa.get('fee_evidence','PUBLIC_MARKET_RATE_PAPER_ASSUMPTION'),
                               b:bb.get('fee_evidence','PUBLIC_MARKET_RATE_PAPER_ASSUMPTION')},
                 fee_rates={a:str(aa['fee_rate']),b:str(bb['fee_rate'])})
        return e

    async def review(self,symbol,buy,sell,read_accounts=False):
        if buy.startswith("dex") or sell.startswith("dex"):
            if not hasattr(self,"dex_transfer"):raise ValueError("DEX-перевод недоступен")
            return await self.dex_transfer.review(symbol,buy,sell,read_accounts)
        if buy not in self.clients or sell not in self.clients:
            raise ValueError('Биржа сейчас недоступна')
        e=await self.evaluate(symbol,buy,sell)
        if e is None:
            raise ValueError('Новые стаканы не подтверждают заданный минимальный спред')
        if read_accounts:
            if all(self.accounts.client(v) is not None for v in (buy,sell)):
                account=await self.accounts.check(symbol,buy,sell)
            else:
                # Public MEXC quotes are enough for PAPER, never for LIVE.
                account=dict(accounts={},reasons=[],ts=self.clock(),execution_authorized=False,
                             fee_evidence='PUBLIC_MARKET_RATE_PAPER_ASSUMPTION')
            e['accounts']=account
            # Read-only balances and account fee rates never grant execution.
            if e['transfer']:
                p=e['transfer'];source=p.get('funding_source',buy)
                if source not in account['accounts'] and source!=buy and 'fee_evidence' not in account:
                    try:
                        account['accounts'][source]=await self.accounts.read(source,symbol)
                    except Exception as exc:
                        e['reasons'].append(source+': не удалось прочитать исходный spot счёт ('+type(exc).__name__+')')
                row=account['accounts'].get(source)
                need=p['funding_amount'] if source!=buy else p['spent']
                if account['accounts'] and (row is None or row['free']['USDT'] is None or D(row['free']['USDT'])<D(need)):
                    e['reasons'].append(source+': не подтверждён свободный spot USDT для маршрута')
                for venue in (buy,sell):
                    actual=account['accounts'].get(venue)
                    if actual and D(actual['taker'])>D(e['fee_rates'][venue]):
                        e['reasons'].append(venue+': тарифная комиссия выше комиссии модели; пересчёт нужен до входа')
            e['reasons'].extend(account['reasons'])
        return e

    def format(self,e,include_code=True):
        if (e.get('transfer') or {}).get('kind')=='DEX_SEQUENTIAL_V1':
            return self.dex_transfer.format(e,include_code)
        p=e.get('transfer')
        lines=[f'🔁 <b>{escape(e["symbol"])} · PAPER</b>',
               escape(e['buy'].upper())+' → '+escape(e['sell'].upper())]
        if p:
            lines += [f"Покупка <b>${float(p['spent']):.2f}</b> · {float(p['buy_qty']):.8g} монет",
                      f"Цены ≈ {float(p['buy_vwap']):.8g} → {float(p['sell_vwap']):.8g}",
                      f"Спред {float(p['spread_pct']):.2f}%",
                      f"Торговые комиссии ≈${float(p['buy_fee'])+float(p['sell_fee']):.3f}",
                      f"Вывод: {float(p['base_route']['fee']):.8g} монет + ${float(p['return_fee']):.3f} USDT",
                      f"Сценарный NET: <b>${float(p['net_after_reserve']):+.3f}</b>",
                      'Сеть: '+escape(p['base_route']['network']),
                      'Продажа после перевода. Цена может измениться.']
        if e['reasons']:
            lines.append('⛔ Вход недоступен: '+escape(str(e['reasons'][0])))
        elif not e.get('trade_configured'):
            lines.append('⛔ Биржи не подключены')
        if include_code and e.get('id'):
            lines.append('Код карточки: '+e['id'])
        return '\n'.join(lines)

    async def queue_channel(self,post):
        await self.store.enqueue('channel:'+str(post['id']),'channel',post)

    async def assess_channel(self,post):
        from .dex_signal_focus import parse_signal
        from .inventory_public_source import Source, same_contract
        try:
            if not 0<=self.clock()-post['timestamp']<=90:return 'Сигнал канала старше 90 секунд; CEX spot не проверяется.'
            s=parse_signal(post['text'])
        except ValueError:
            return 'Из поста не удалось выделить точный новый токен; CEX spot не проверяется.'
        matches=[c for c in self.candidates() if c[1]==s['base']+'/USDT']
        for _,symbol,a,b in matches[:3]:
            try:
                e=await self.evaluate(symbol,a,b)
                if not e or e['transfer'] is None or e['reasons']:continue
                for venue in (a,b):
                    rows=Source.networks_for(self.currency_cache[venue][1].get(s['base'],{}))
                    native=rows.get(e['transfer']['base_route']['network'],{})
                    if not native.get('contract') or not same_contract(native['contract'],s['asset'],e['transfer']['base_route']['network']):
                        raise ValueError('Токен канала не совпадает с CEX-контрактом')
                key='spot:'+symbol+':'+a+':'+b+':'+str(int(self.clock()//300))
                e['id']=hashlib.sha256(key.encode()).hexdigest()[:16]
                await self.store.enqueue(key,'spot',e)
                return 'Независимый CEX spot маршрут с тем же точным контрактом найден. Отдельная карточка проверяется и отправляется. Цена канала не использована как котировка.'
            except (ValueError,KeyError,IndexError):continue
        return 'Независимый CEX spot маршрут с точным контрактом, свежими стаканами и полной экономикой сейчас не найден. Цифры канала не подменяют сделку.'

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
                    result+='\n\n🔎 CEX spot: '+await self.assess_channel(item['payload'])
                else:
                    result=self.format(item['payload'])
                await self.store.result(item['key'],result)
            # Ambiguous Telegram timeout may duplicate a message, never a trade.
            if self.actionable_only and (item['kind']=='channel' or item['payload'].get('reasons')
                    or not item['payload'].get('trade_configured')):
                await self.store.delivered(item['key'])
                continue
            delivery=result
            if item['kind']=='channel' and self.clock()-item['ts']>90:
                delivery='⚠ Задержанная доставка. Проверка ниже устарела; НЕ использовать для текущего входа.\n\n'+delivery
            await self.notify(delivery)
            await self.store.delivered(item['key'])

    async def cycle(self):
        async with self.lock:
            await self.paper.monitor(self)
            if self.position_card:
                await self.paper.sync_cards(self.position_card,self.remove_position_card)
            await self.paper.flush(self.notify,quiet_lifecycle=self.position_card is not None)
            if not await self.store.enabled():
                return []
            if not self.entry_enabled:
                return []
            if self.loader is None and self.names:
                self.loader=asyncio.create_task(self.load_loop())
            await self.flush()
            # Bounded book work; failed top routes don't starve others forever.
            candidates=self.candidates((await self.store.control())['min_spread'])
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
                    e['id']=hashlib.sha256(key.encode()).hexdigest()[:16]
                    await self.store.enqueue(key,'spot',e)
                    control=await self.store.control()
                    if (control['mode']=='auto' and e.get('trade_configured') and e.get('transfer')
                            and not e['reasons'] and not self.paper.positions):
                        await self.paper.open(dict(signal=e,transfer=e['transfer']))
                        if self.position_card:
                            await self.paper.sync_cards(self.position_card,self.remove_position_card)
                else:
                    await self.store.enqueue(key,'miss',{})
                    await self.store.delivered(key)
                await self.flush()
                break
            self.last_scan=self.clock()
        return []

    def status(self):
        ok=[v for v in self.clients if self.health.get(v)=='OK' and v in self.tickers and 0<=self.clock()-self.tickers[v][0]<=90]
        age = self.clock()-self.last_scan if self.last_scan else None
        state = f'{max(0,int(age))} сек. назад' if age is not None else 'запускается'
        text = ('📡 <b>Покупка → перевод → продажа</b>\n\n'
                'PAPER · виртуальный депозит $250\n'
                'Котировки: '+escape(', '.join(ok) or 'ожидаем')+'\n'
                f'Обновление: {state}')
        if hasattr(self,'dex_transfer'):
            dx=self.dex_transfer
            text+=f'\n\nBybit ↔ DEX · проверок {dx.checked}\n'+escape(dx.last_reason[:150])
            e=getattr(dx,'last_economics',None)
            if e:text+=f'\n{escape(e["symbol"])} · спред {e["spread_pct"]:+.2f}% · стресс-NET ${e["stress_net"]:+.3f}'
        elif len(self.paper_venues)<2:
            text += '\n\nДля PAPER-цикла нужны две подключённые spot-биржи.'
        return text

    async def close(self):
        tasks=([self.loader] if self.loader else [])+list(self.workers.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        await asyncio.gather(*(c.close() for v,c in self.clients.items() if v not in self.shared_clients),return_exceptions=True)
