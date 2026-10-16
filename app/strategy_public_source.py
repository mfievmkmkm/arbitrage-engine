"""Optional read-only public capture adapter for explicitly registered routes.

Uses existing paced clients. No new clients, orders, wallet RPC, borrowing or
transfers. Config supplies economic assumptions, never invented book prices.
DEX/borrow/financing/margin evidence needs a dedicated provider when dynamic.
The output remains an indicative Paper snapshot, never LIVE release evidence.
"""
import asyncio
import copy
import logging
import secrets
import time
from .public_books import normalize
from .strategy_math import D, ZERO, ONE, Invalid, flag, nonnegative, positive, plain
from .strategy_catalog import CATALOG


class Source:
    def __init__(self, clients, routes, funding=None, dex_book=None, clock=time.time):
        self.clients,self.routes,self.funding,self.dex_book,self.clock=clients,routes,funding,dex_book,clock
        self.gate=asyncio.Semaphore(2)
        if not isinstance(routes,list) or len(routes)>30:
            raise Invalid('PUBLIC_STRATEGY_ROUTES_LIMIT')
        self.by_key={}
        for route in routes:
            key=route['strategy']+':'+route['route']
            if route['strategy'] not in CATALOG or key in self.by_key:
                raise Invalid('PUBLIC_ROUTE_DUPLICATE_OR_UNKNOWN')
            if not 2<=len(route['books'])<=6:
                raise Invalid('PUBLIC_ROUTE_LEG_COUNT')
            self.by_key[key]=route

    async def _book(self, spec, max_age):
        if spec['kind']=='dex':
            if self.dex_book is None:
                raise Invalid('DEX_QUOTE_ADAPTER_REQUIRED')
            return await self.dex_book(copy.deepcopy(spec))
        client_key=spec['client']
        if client_key not in self.clients:
            raise Invalid('PUBLIC_CLIENT_UNAVAILABLE')
        client=self.clients[client_key]
        market=client.markets.get(spec['symbol'])
        if not market or market.get('active') is not True:
            raise Invalid('PUBLIC_MARKET_INACTIVE_OR_UNKNOWN')
        flag(spec,'identity_verified')
        if market.get('base')!=spec['base'] or market.get('quote')!=spec['quote']:
            raise Invalid('PUBLIC_MARKET_IDENTITY_MISMATCH')
        if spec['kind']=='spot':
            if market.get('spot') is not True:
                raise Invalid('PUBLIC_MARKET_KIND_MISMATCH')
        else:
            kind='perp' if market.get('swap') else 'future' if market.get('future') else market.get('optionType') if market.get('option') else None
            if kind!=spec['kind'] or market.get('linear') is not True or market.get('settle')!=spec['quote']:
                raise Invalid('PUBLIC_DERIVATIVE_MISMATCH')
        precision=(market.get('precision') or {}).get('amount')
        if precision is None:
            raise Invalid('PUBLIC_LOT_UNKNOWN')
        mode=getattr(client,'precisionMode',None)
        if mode==4: # CCXT TICK_SIZE
            step=positive(precision)
        elif mode==2 and D(precision)==int(precision) and 0<=int(precision)<=18:
            step=D(10)**(-int(precision))
        else:
            raise Invalid('PUBLIC_PRECISION_MODE_UNSUPPORTED')
        limits=market.get('limits') or {}
        minimum=(limits.get('amount') or {}).get('min')
        min_cost=(limits.get('cost') or {}).get('min')
        if minimum is None or min_cost is None:
            raise Invalid('PUBLIC_MINIMUMS_UNKNOWN')
        rate=market.get('taker')
        if rate is None:
            raise Invalid('PUBLIC_FEES_UNKNOWN')
        if spec.get('fee_currency')!='quote' or spec.get('fee_currency_verified') is not True:
            raise Invalid('PUBLIC_FEE_CURRENCY_UNKNOWN')
        size=positive(market['contractSize']) if market.get('contract') else ONE
        async with self.gate:
            started=self.clock()
            raw=await asyncio.wait_for(client.fetch_order_book(spec['symbol'],limit=20),8)
            received=self.clock()
        raw=normalize(raw,spec['symbol'],started,received,float(max_age),raw.get('data_source','REST'))
        row=dict(id=spec['id'],venue=spec['venue'],base=spec['base'],quote=spec['quote'],
                 asset_id=spec['asset_id'],quote_asset_id=spec['quote_asset_id'],kind=spec['kind'],
                 linear=True,settle=spec['quote'],identity_verified=True,
                 ts=D(raw['timestamp'])/1000,step=step,multiplier=size,
                 min_qty=nonnegative(minimum),min_notional=nonnegative(min_cost),fee_rate=nonnegative(rate),fee_currency='quote',
                 bids=[[D(p),D(q)*size] for p,q in raw['bids']],
                 asks=[[D(p),D(q)*size] for p,q in raw['asks']])
        if market.get('expiry') is not None:
            row['expiry']=D(market['expiry'])/1000
        # Settlement/exercise are verified registry facts, not guessed from a symbol.
        for key in ('settlement_verified','settlement_method','option_style','margin_verified','margin_per_base'):
            if key in spec:
                row[key]=spec[key]
        if market.get('strike') is not None:
            row['strike']=market['strike']
        return plain(row)

    async def snapshot(self, key, values):
        route=self.by_key[key]
        s=copy.deepcopy(route)
        p=values
        s['books']=await asyncio.gather(*(self._book(x,p['max_age']) for x in route['books']))
        now=D(self.clock())
        s.update(ts=str(now),environment='paper',source_quality='replay',source_origin='public_capture')
        perps=[(spec,row) for spec,row in zip(route['books'],s['books']) if row['kind']=='perp']
        s['funding_events']=[]
        s['funding_coverage_until']={}
        s['funding_settled_until']={}
        s['funding_calendar_verified']=not perps
        s['funding_settlement_verified']=not perps
        if perps:
            if self.funding is None:
                raise Invalid('PUBLIC_FUNDING_ADAPTER_REQUIRED')
            horizon=positive(s.get('horizon_seconds',p['max_hold']))
            for spec,row in perps:
                snap=await self.funding.get(spec['venue'],spec['symbol'])
                first,interval,rate=D(snap.next_ts)/1000,positive(snap.interval_hours)*3600,D(snap.rate)
                if first<=now or abs(rate)>=1:
                    raise Invalid('PUBLIC_FUNDING_CALENDAR_INVALID')
                ts=first
                count=0
                while ts<=now+horizon:
                    count+=1
                    if count>1500:
                        raise Invalid('PUBLIC_FUNDING_EVENT_LIMIT')
                    s['funding_events'].append(dict(instrument=row['id'],ts=str(ts),rate=str(rate),notional_price=row['asks'][0][0]))
                    ts+=interval
                s['funding_coverage_until'][row['id']]=str(now+horizon)
                # Public funding calendars do not prove account cashflows, even
                # when there were no events in the admission horizon.
                s['funding_settled_until'][row['id']]=str(now)
            s['funding_calendar_verified']=True
            s['funding_settlement_verified']=False
        return plain(s)


class Runtime:
    def __init__(self,lab,source,allow_entries=lambda route:False,interval=30):
        self.lab,self.source,self.allow_entries=lab,source,allow_entries
        self.interval=max(10,float(interval))
        self.task=None
        self.errors={}

    async def refresh(self,key):
        return await self.source.snapshot(key,self.lab.state['settings']['policy'])

    async def cycle(self):
        for key in self.source.by_key:
            try:
                s=await self.refresh(key)
                stamp=self.source.clock()
                await self.lab.process(dict(id='public-'+secrets.token_hex(12),type='snapshot',ts=stamp,snapshot=s),
                                       now=stamp,allow_entries=self.allow_entries(s))
                self.errors.pop(key,None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.errors[key]=type(exc).__name__+':'+str(exc)[:150]
                logging.getLogger('arbitrage').warning('Strategy public capture %s: %s',key,self.errors[key])
                await self.lab.source_error(key,self.errors[key],self.source.clock())

    async def _loop(self):
        while True:
            try:
                await self.cycle()
                self.errors.pop('_runtime',None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.errors['_runtime']=type(exc).__name__
                logging.getLogger('arbitrage').exception('Strategy public capture runtime failed; positions remain reserved')
            await asyncio.sleep(self.interval)

    def start(self):
        self.task=asyncio.create_task(self._loop())

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task,return_exceptions=True)
