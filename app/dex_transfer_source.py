"""Bybit spot <-> DEX sequential PAPER. GET/read APIs only; no signer.

Quotes qualify an entry scenario; they never lock the later sale price.
Initial scope: same-chain ERC20 tokens on Ethereum/BSC, USDT settlement.
"""
import asyncio
import copy
from decimal import Decimal, ROUND_DOWN
from .strategy_math import D, ONE, ZERO, plain, common_quantity
from .inventory_paper import fill, day
from .inventory_public_source import step
from .transfer_signal_math import limits
from .dex_virtual_paper_source import Source as QuoteSource
from .dex_cex_identity import ALIASES

STABLES={1:('0xdac17f958d2ee523a2206206994597c13d831ec7',6,'ETH'),
         56:('0x55d398326f99059ff775485246999027b3197955',18,'BNB')}
KIND='DEX_SEQUENTIAL_V1'


def dex(venue):return venue in ('dex1','dex56')

def venue_label(venue):return {'dex1':'DEX · Ethereum','dex56':'DEX · BSC','bybit':'Bybit Spot'}.get(venue,venue.upper())


def amount_raw(amount,decimals):
    return int((D(amount)*D(10)**decimals).to_integral_value(rounding=ROUND_DOWN))


class Source:
    def __init__(self,feed,paired):
        self.feed,self.paired,self.clock=feed,paired,feed.clock
        self.paper=feed.paper;self.store=feed.store
        self.metadata=None;self.metadata_at=0;self.metadata_lock=asyncio.Lock()
        self.metadata_retry=0;self.metadata_error="Bybit: сети недоступны"
        self.fees={};self.routes={}
        self.last_reason='Ожидаем проверку';self.checked=0
        self.quote=QuoteSource.__new__(QuoteSource)
        self.quote.provider=paired.paper.source.price_providers.get("KyberSwap",paired.paper.source.provider)
        self.quote.clock=self.clock

    def catalog(self):
        self.routes={}
        client=self.feed.clients.get('bybit');out=[]
        if not client:return []
        for original in self.paired.scanner.catalog.values():
            chain=original['chain_id'];symbol=original['base']+'/USDT'
            m=client.markets.get(symbol,{})
            if (chain not in STABLES or m.get('spot') is not True or m.get('active') is not True
                    or m.get('contract') or m.get('base')!=original['base'] or m.get('quote')!='USDT'):
                continue
            stable,decimals,native=STABLES[chain]
            if original['asset'].lower()==stable:continue
            r=dict(original,stable=stable,stable_decimals=decimals,stable_symbol='USDT',
                   spot_symbol=symbol,native_spot=native+'/USDT',mode='paired')
            key=(symbol,'dex'+str(chain));self.routes[key]=r;out.append(r)
        return out

    def route_for(self,symbol,a,b):
        if {a,b} not in ({'bybit','dex1'},{'bybit','dex56'}):
            raise ValueError('DEX_TRANSFER_VENUES_INVALID')
        self.catalog()
        r=self.routes.get((symbol,a if dex(a) else b))
        if r is None:raise ValueError('DEX_TRANSFER_ROUTE_UNAVAILABLE')
        return copy.deepcopy(r)

    async def currencies(self):
        async with self.metadata_lock:
            if self.metadata is not None and 0<=self.clock()-self.metadata_at<60:return self.metadata
            if self.clock()<self.metadata_retry:raise ValueError(self.metadata_error)
            c=self.feed.accounts.client('bybit')
            if c is None or c.has.get('fetchCurrencies') is not True:
                raise ValueError('Bybit: нужен API чтения сетей')
            try:
                rows=await asyncio.wait_for(c.fetch_currencies(),10)
            except asyncio.CancelledError:raise
            except Exception as exc:
                self.metadata_error='Bybit: сети недоступны ('+type(exc).__name__+')'
                self.metadata_retry=self.clock()+30
                raise ValueError(self.metadata_error) from None
            if not isinstance(rows,dict) or not rows:raise ValueError('Bybit: нет свежих данных сетей')
            wanted={'USDT'} | {r['base'] for r in self.paired.scanner.catalog.values()}
            active=self.paper.state.get('position')
            if active and active.get('kind')==KIND:wanted.add(active['plan']['route']['base'])
            self.metadata={coin:row for coin,row in rows.items() if coin in wanted}
            self.metadata_at=self.clock()
            return self.metadata

    async def networks(self,r):
        rows=await self.currencies();control=await self.store.control();out={}
        for name,contract in ((r['base'],r['asset']),('USDT',r['stable'])):
            cur=rows.get(name) or {};matches=[]
            if cur.get('deposit') is False or cur.get('withdraw') is False:
                raise ValueError('Bybit: ввод/вывод '+name+' остановлен')
            for key,n in (cur.get('networks') or {}).items():
                raw=n.get('info') or {}
                chains={ALIASES.get(str(x).upper()) for x in (key,n.get('network'),n.get('id'),raw.get('chain'))}-{None}
                address=n.get('contract') or raw.get('contractAddress')
                if chains=={r['chain_id']} and isinstance(address,str) and address.lower()==contract.lower():
                    matches.append(n)
            if len(matches)!=1:raise ValueError('Bybit: сеть/контракт '+name+' не подтверждены')
            n=matches[0];raw=n.get('info') or {}
            if n.get('active') is False or n.get('deposit') is not True or n.get('withdraw') is not True:
                raise ValueError('Bybit: ввод/вывод '+name+' недоступен')
            percentage=n.get('percentage')
            if raw.get('withdrawPercentageFee') not in (None,''):percentage=D(raw['withdrawPercentageFee'])!=0
            fee=n.get('fee');mins=n.get('limits') or {}
            wd=(mins.get('withdraw') or {}).get('min',raw.get('withdrawMin'))
            dep=(mins.get('deposit') or {}).get('min',raw.get('depositMin'))
            precision=n.get('precision')
            if precision is None and raw.get('minAccuracy') is not None:
                accuracy=int(raw['minAccuracy'])
                if not 0<=accuracy<=36:raise ValueError('Bybit: неизвестна точность вывода')
                precision=D(10)**-accuracy
            if percentage is not False or any(x is None for x in (fee,wd,dep,precision)):
                raise ValueError('Bybit: неизвестны комиссия, точность или минимумы '+name)
            if min(D(fee),D(wd),D(dep))<0 or D(precision)<=0:raise ValueError('Bybit: неверные параметры сети')
            deposit=n.get('deposit_fee');dep_pct=n.get('deposit_fee_percentage')
            source='ACCOUNT_NETWORK_API'
            if deposit is None or dep_pct is None:
                # Explicit operator PAPER parameter, never silently assume zero.
                deposit=control.get('dex_deposit_fee_pct');dep_pct=True
                source='OPERATOR_PAPER_PERCENT'
                if deposit is None or not 0<=self.clock()-control.get('dex_deposit_fee_set_at',0)<86400:
                    raise ValueError('Укажи комиссию ввода Bybit в параметрах PAPER')
                deposit=D(deposit)/100
            if type(dep_pct) is not bool or D(deposit)<0 or (dep_pct is True and D(deposit)>=1):raise ValueError('Bybit: комиссия ввода неизвестна')
            out[name]=dict(contract=contract.lower(),chain_id=r['chain_id'],fee=str(D(fee)),
                withdraw_min=str(D(wd)),deposit_min=str(D(dep)),step=str(D(precision)),
                withdraw_max=raw.get('withdrawMax'),
                deposit_fee=str(D(deposit)),deposit_percentage=dep_pct,fee_source=source,
                observed_at=self.metadata_at)
        return out

    @staticmethod
    def received(net,amount):
        amount=D(amount)
        if amount<D(net['deposit_min']):raise ValueError('Bybit: депозит меньше минимума')
        fee=amount*D(net['deposit_fee']) if net['deposit_percentage'] is True else D(net['deposit_fee'])
        if fee>=amount:raise ValueError('Bybit: депозит меньше комиссии')
        return amount-fee

    @staticmethod
    def withdrawn(net,amount):
        qty=common_quantity(D(amount),[D(net['step'])])
        maximum=net.get('withdraw_max')
        if maximum not in (None,'-1',-1) and qty>D(maximum):raise ValueError('Bybit: превышен максимум вывода')
        if qty<D(net['withdraw_min']) or qty<=D(net['fee']):
            raise ValueError('Bybit: вывод меньше минимума/комиссии')
        return qty-D(net['fee']),D(amount)-qty

    async def book(self,r):
        symbol=r['spot_symbol'];c=self.feed.accounts.client('bybit')
        market=(self.feed.clients.get('bybit').markets.get(symbol,{}) if self.feed.clients.get('bybit') else {})
        if market.get('spot') is not True or market.get('active') is not True or market.get('contract') or market.get('base')!=r['base'] or market.get('quote')!='USDT':
            raise ValueError('Bybit: spot-рынок недоступен')
        if c is None or c.has.get('fetchTradingFee') is not True:raise ValueError('Bybit: нет account-read spot комиссии')
        old=self.fees.get(symbol)
        if not old or not 0<=self.clock()-old[0]<30:
            try:
                f=await asyncio.wait_for(c.fetch_trading_fee(symbol),8)
            except asyncio.CancelledError:raise
            except Exception as exc:raise ValueError('Bybit: комиссия spot недоступна ('+type(exc).__name__+')') from None
            if f.get('symbol')!=symbol or f.get('taker') is None or not 0<=D(f['taker'])<1:
                raise ValueError('Bybit: комиссия spot не подтверждена')
            old=(self.clock(),D(f['taker']));self.fees[symbol]=old
        b=await self.feed.book('bybit',symbol)
        b['fee_rate']=old[1];return b

    async def price(self,r,sell_asset,quantity):
        decimals=r['asset_decimals'] if sell_asset else r['stable_decimals']
        raw=amount_raw(quantity,decimals)
        if raw<=0:raise ValueError('DEX: размер ниже точности токена')
        return await self.quote.price(r,sell_asset,raw)

    async def gas(self,r,q,transfer=False,swap=False):
        market=self.feed.clients['bybit'].markets.get(r['native_spot'],{})
        if market.get('spot') is not True or market.get('active') is not True or market.get('contract'):
            raise ValueError('Bybit: нет активного spot native/USDT для gas')
        b=await self.feed.book('bybit',r['native_spot'])
        price=D(b['asks'][0][0]);raw=D(q.get('gas_price_raw'))
        if raw<=0 or not 0<=self.clock()-float(q['ts'])<=15 or not ZERO<=D(self.clock())-b['ts']<=3:raise ValueError('DEX: нет свежего gas/native price')
        # Declared PAPER gas-unit assumptions, not eth_estimateGas or receipts.
        units=(200000 if transfer else 0)+(100000 if swap else 0)
        fee=raw*D(units)/D(10)**18*price
        if swap:fee+=D(q['network_fee_raw'])/D(10)**18*price+D(q.get('network_fee_usd_extra',0))
        if fee<0:raise ValueError('DEX: неизвестен gas')
        return fee

    async def gas_probe(self,r):
        return await self.price(r,False,5)

    async def buy(self,r,venue,budget,networks):
        budget=D(budget)
        if dex(venue):
            q=await self.price(r,False,budget)
            qty=D(q['virtual_min_buy_raw'])/D(10)**r['asset_decimals']
            return plain(dict(qty=qty,cost=budget,buy_fee=0,dust=0,ts=q['ts'],
                              gas=await self.gas(r,q,swap=True),quote=q))
        b=await self.book(r);client=self.feed.clients['bybit'];m=client.markets[r['spot_symbol']]
        qty=common_quantity(budget/D(b['asks'][0][0]),[b['step']]);filled,cash,_=fill(b,qty,'buy')
        if filled!=qty or qty<=0:raise ValueError('Bybit: недостаточно глубины покупки')
        limits(m,qty,cash)
        # Bybit spot buy fee is taken from the purchased BASE in this model.
        fee_qty=qty*b['fee_rate'];received=qty-fee_qty
        raw=amount_raw(received,r['asset_decimals']);received=D(raw)/D(10)**r['asset_decimals']
        return plain(dict(qty=received,cost=cash,buy_fee=fee_qty*cash/qty,
                          dust=qty-fee_qty-received,ts=b['ts'],gas=0,quote=None))

    async def sell(self,r,venue,quantity):
        quantity=D(quantity)
        if dex(venue):
            q=await self.price(r,True,quantity)
            used=D(q['sell_amount_raw'])/D(10)**r['asset_decimals']
            return plain(dict(sold=used,cash=D(q['virtual_min_buy_raw'])/D(10)**r['stable_decimals'],
                              fee=0,dust=quantity-used,gas=await self.gas(r,q,swap=True),ts=q['ts'],quote=q))
        b=await self.book(r);qty=common_quantity(quantity,[b['step']]);sold,cash,fee=fill(b,qty,'sell')
        if sold>0:limits(self.feed.clients['bybit'].markets[r['spot_symbol']],sold,cash)
        return plain(dict(sold=sold,cash=cash-fee,fee=fee,dust=quantity-qty,gas=0,ts=b['ts'],quote=None))

    async def transfer(self,r,asset,src,dst,amount,networks,q=None):
        amount=D(amount);gas=ZERO;dust=ZERO
        if src==dst:return plain(dict(amount=amount,gas=0,dust=0,fee=0))
        if src=='bybit':
            received,dust=self.withdrawn(networks[asset],amount);fee=amount-received-dust
        else:
            q=q or await self.gas_probe(r)
            gas=await self.gas(r,q,transfer=True)
            received=self.received(networks[asset],amount);fee=amount-received
        return plain(dict(amount=received,gross=amount if src!='bybit' else received,gas=gas,dust=dust,fee=fee))

    async def review(self,symbol,buy,sell,read_accounts=False,route=None,reserved=None):
        r=copy.deepcopy(route or self.route_for(symbol,buy,sell))
        control=await self.store.control();networks=await self.networks(r)
        if 'bybit' not in self.feed.trade_venues:raise ValueError('Подключи Bybit spot API')
        budget=D(control['budget']);state=self.paper.state
        if not reserved and (D(state['realized'])<=D('-2.5') or D(state['daily'].get(day(self.clock()),0))<=D('-1.25')):
            raise ValueError('Достигнут лимит убытка PAPER')
        source=reserved['source'] if reserved else self.paper.funding_source(buy,budget)
        if source not in ('bybit','dex'+str(r['chain_id'])):raise ValueError('Выбери старт PAPER на Bybit; нет USDT для этой сети')
        buy_budget=budget
        funding=(dict(amount=reserved['purchase_budget'],gas=str(reserved['gas_used']),dust='0',fee=reserved['funding_fee']) if reserved else
                 await self.transfer(r,'USDT',source,buy,budget,networks) if source!=buy else dict(amount=str(budget),gas='0',dust='0',fee='0'))
        buy_budget=D(funding['amount'])
        entry=await self.buy(r,buy,buy_budget,networks)
        base=await self.transfer(r,r['base'],buy,sell,entry['qty'],networks,entry['quote'])
        sale=await self.sell(r,sell,base['amount'])
        returned=await self.transfer(r,'USDT',sell,source,sale['cash'],networks,sale['quote']) if sell!=source else dict(amount=sale['cash'],gas='0',dust='0',fee='0')
        gas=sum((D(x['gas']) for x in (funding,entry,base,sale,returned)),ZERO)
        spent=D(reserved['original_budget']) if reserved else budget-D(funding['dust'])
        leftover=buy_budget-D(entry['cost'])
        net=D(returned['amount'])+leftover-spent-gas
        buy_vwap=D(entry['cost'])/D(entry['qty']);sell_vwap=D(sale['cash'])/D(sale['sold']) if D(sale['sold'])>0 else ZERO
        spread=(sell_vwap/buy_vwap-ONE)*100
        stress=net-D(sale['cash'])*D(control['stress_drop'])/100-D('.10')
        reasons=[]
        if D(sale['sold'])+D(sale['dust'])<D(base['amount']):reasons.append('Недостаточно глубины предполагаемой продажи')
        if not D(control['min_spread'])<=spread<=D(control['max_spread']):reasons.append('Спред вне заданных границ')
        if stress<D(control['min_net']):reasons.append('Стресс-NET после переводов и gas ниже минимума')
        if any(not 0<=self.clock()-float(x['ts'])<=3 for x in (entry,sale)):reasons.append('Нужны новые цены входа')
        reserve=max(D('.10'),gas*2)
        if not reserved and D(state['balances'].get(source,'0'))<budget+reserve:reasons.append('Не хватает виртуального USDT с резервом gas')
        p=plain(dict(kind=KIND,route=r,source=source,budget=budget,gas_reserve=reserve,
                     buy_qty=entry['qty'],received_qty=base['amount'],sell_qty=sale['sold'],spent=entry['cost'],
                     buy_fee=entry['buy_fee'],sell_fee=sale['fee'],buy_vwap=buy_vwap,sell_vwap=sell_vwap,
                     base_route=dict(network=str(r['chain_id']),fee=base['fee']),return_fee=returned['fee'],
                     spread_pct=spread,net_after_reserve=net-D('.10'),stress_net=stress,reasons=reasons,
                     preview_buy=entry,preview_base=base,
                     costs=dict(gas=gas,funding=funding,base=base,returned=returned),network_evidence=networks,
                     delay_evidence='UNMEASURED_900S_PER_TRANSFER',gas_evidence='200K_TRANSFER_100K_APPROVAL_PAPER_UNITS'))
        return dict(symbol=symbol,buy=buy,sell=sell,ts=min(float(entry['ts']),float(sale['ts'])),
                    spread_pct=str(spread),reasons=reasons,transfer=p,trade_configured=True,mode=control['mode'])

    def format(self,e,include_code=True):
        from html import escape
        p=e['transfer']
        text=(f'🔁 <b>{escape(e["symbol"])} · PAPER</b>\n'
              f'{escape(venue_label(e["buy"]))} → {escape(venue_label(e["sell"]))}\n\n'
              f'Покупка до ${float(p["budget"]):g} · спред {float(p["spread_pct"]):.2f}%\n'
              f'Сценарный NET ${float(p["net_after_reserve"]):+.3f}\n'
              f'Gas ≈${float(p["costs"]["gas"]):.3f} · сеть {p["route"]["chain_id"]}\n'
              'Продажа после зачисления. Цена не закреплена.')
        if e['reasons']:text+='\n⛔ '+escape(e['reasons'][0])
        if include_code and e.get('id'):text+='\nКод карточки: '+e['id']
        return text
