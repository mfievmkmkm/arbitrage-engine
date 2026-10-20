"""CEX spot <-> DEX sequential PAPER. GET/read APIs only; no signer.

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
from .evidence_error import EvidenceError

STABLES={1:('0xdac17f958d2ee523a2206206994597c13d831ec7',6,'ETH'),
         56:('0x55d398326f99059ff775485246999027b3197955',18,'BNB')}
KIND='DEX_SEQUENTIAL_V1'


def dex(venue):return venue in ('dex1','dex56')

def venue_label(venue):return {'dex1':'DEX · Ethereum','dex56':'DEX · BSC','bybit':'Bybit Spot','mexc':'MEXC Spot'}.get(venue,venue.upper())


def amount_raw(amount,decimals):
    return int((D(amount)*D(10)**decimals).to_integral_value(rounding=ROUND_DOWN))


class Source:
    def __init__(self,feed,paired,venue="bybit"):
        self.feed,self.paired,self.clock=feed,paired,feed.clock
        if venue not in ("bybit","mexc"):raise ValueError("DEX_TRANSFER_CEX_UNSUPPORTED")
        self.venue=venue;self.external_sources={}
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
        client=self.feed.clients.get(self.venue);out=[]
        if not client:return []
        # A spot transfer does not require a futures listing. Include verified
        # Bybit reference routes as well as the paired scanner's routes.
        candidates=list(self.paired.scanner.catalog.values())
        legacy=getattr(self.paired,'legacy',{})
        bybit=legacy.get('dex_bybit' if self.venue=='bybit' else 'dex_channel') if isinstance(legacy,dict) else None
        if bybit is not None:
            candidates.extend(r for r in bybit.source.routes if r.get('cex_venue')==self.venue)
        for original in candidates:
            chain=original['chain_id'];symbol=original['base']+'/USDT'
            m=client.markets.get(symbol,{})
            if (chain not in STABLES or m.get('spot') is not True or m.get('active') is not True
                    or m.get('contract') or m.get('base')!=original['base'] or m.get('quote')!='USDT'):
                continue
            stable,decimals,native=STABLES[chain]
            if original['asset'].lower()==stable:continue
            if (symbol,'dex'+str(chain)) in self.routes:
                continue
            r=dict(original,stable=stable,stable_decimals=decimals,stable_symbol='USDT',
                   spot_symbol=symbol,native_spot=native+'/USDT',spot_venue=self.venue,mode='paired')
            key=(symbol,'dex'+str(chain));self.routes[key]=r;out.append(r)
        return out

    def route_for(self,symbol,a,b):
        if {a,b} not in ({self.venue,'dex1'},{self.venue,'dex56'}):
            raise ValueError('DEX_TRANSFER_VENUES_INVALID')
        self.catalog()
        r=self.routes.get((symbol,a if dex(a) else b))
        if r is None:raise ValueError('DEX_TRANSFER_ROUTE_UNAVAILABLE')
        return copy.deepcopy(r)

    async def currencies(self):
        async with self.metadata_lock:
            if self.metadata is not None and 0<=self.clock()-self.metadata_at<60:return self.metadata
            if self.clock()<self.metadata_retry:raise ValueError(self.metadata_error)
            c=self.feed.accounts.client(self.venue)
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
            wanted={'USDT'} | {r['base'] for r in self.catalog()}
            active=self.paper.state.get('position')
            if active and active.get('kind')==KIND:wanted.add(active['plan']['route']['base'])
            self.metadata={coin:row for coin,row in rows.items() if coin in wanted}
            self.metadata_at=self.clock()
            return self.metadata

    async def networks(self,r,operations=None):
        if operations is not None and not any(operations.values()):return {}
        rows=await self.currencies();control=await self.store.control();out={}
        for name,contract in ((r['base'],r['asset']),('USDT',r['stable'])):
            cur=rows.get(name) or {};matches=[]
            required=(operations if operations is not None else {}).get(name,{'deposit','withdraw'} if operations is None else set())
            if not required:continue
            if any(cur.get(action) is False for action in required):
                raise EvidenceError('Bybit: нужный ввод/вывод '+name+' остановлен',asset=name,
                    required_operations=sorted(required),operation_status={k:cur.get(k) for k in required},status_source='CURRENCY')
            for key,n in (cur.get('networks') or {}).items():
                raw=n.get('info') or {}
                chains={ALIASES.get(str(x).upper()) for x in (key,n.get('network'),n.get('id'),raw.get('chain'))}-{None}
                address=n.get('contract') or n.get('contractAddress') or raw.get('contractAddress') or raw.get('contract')
                exact=chains=={r['chain_id']} and isinstance(address,str) and address.lower()==contract.lower()
                native_eth=(name=='ETH' and r['chain_id']==1 and chains=={1} and not address
                            and contract.lower()=='0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2')
                if exact or native_eth:
                    matches.append(dict(n,_native_wrap=native_eth))
            if len(matches)!=1:
                raise EvidenceError('Bybit: сеть/контракт '+name+' не подтверждены',asset=name,
                    expected_chain=r['chain_id'],expected_contract=contract,matching_networks=len(matches),
                    available_networks=list((cur.get('networks') or {}).keys()),required_operations=sorted(required))
            n=matches[0];raw=n.get('info') or {}
            if n.get('active') is False or any(n.get(action) is not True for action in required):
                raise EvidenceError('Bybit: нужный ввод/вывод '+name+' недоступен',asset=name,
                    required_operations=sorted(required),operation_status={k:n.get(k) for k in required},
                    active=n.get('active'),network=n.get('network'),status_source='NETWORK')
            def number(value):
                try:return D(value)
                except ValueError:raise ValueError('Bybit: неверное числовое поле сети '+name) from None
            percentage=n.get('percentage')
            if raw.get('withdrawPercentageFee') not in (None,''):percentage=number(raw['withdrawPercentageFee'])!=0
            fee=n.get('fee');mins=n.get('limits') or {}
            wd=(mins.get('withdraw') or {}).get('min',raw.get('withdrawMin'))
            dep=(mins.get('deposit') or {}).get('min',raw.get('depositMin'))
            precision=n.get('precision')
            if precision is None and raw.get('minAccuracy') is not None:
                accuracy=int(raw['minAccuracy'])
                if not 0<=accuracy<=36:raise ValueError('Bybit: неизвестна точность вывода')
                precision=D(10)**-accuracy
            if ('withdraw' in required and (percentage is not False or any(x is None for x in (fee,wd,precision)))) or ('deposit' in required and dep is None):
                raise ValueError('Bybit: неизвестны комиссия, точность или минимумы '+name)
            if ('withdraw' in required and (min(number(fee),number(wd))<0 or number(precision)<=0)) or ('deposit' in required and number(dep)<0):
                raise ValueError('Bybit: неверные параметры сети')
            deposit=n.get('deposit_fee');dep_pct=n.get('deposit_fee_percentage')
            source='ACCOUNT_NETWORK_API'
            if 'deposit' in required and (deposit is None or dep_pct is None):
                # Explicit operator PAPER parameter, never silently assume zero.
                fee_key='dex_deposit_fee_pct' if self.venue=='bybit' else 'mexc_deposit_fee_pct'
                deposit=control.get(fee_key);dep_pct=True
                source='OPERATOR_PAPER_PERCENT'
                if deposit is None or not 0<=self.clock()-control.get(fee_key.replace('_pct','_set_at'),0)<86400:
                    raise ValueError('Укажи комиссию ввода Bybit в параметрах PAPER')
                deposit=D(deposit)/100
            if 'deposit' in required and (type(dep_pct) is not bool or D(deposit)<0 or (dep_pct is True and D(deposit)>=1)):
                raise ValueError('Bybit: комиссия ввода неизвестна')
            out[name]=dict(contract=contract.lower(),chain_id=r['chain_id'],fee=str(D(fee)) if fee is not None else None,
                withdraw_min=str(D(wd)) if wd is not None else None,deposit_min=str(D(dep)) if dep is not None else None,step=str(D(precision)) if precision is not None else None,
                withdraw_max=raw.get('withdrawMax'),
                deposit_fee=str(D(deposit)) if deposit is not None else None,deposit_percentage=dep_pct,fee_source=source,
                observed_at=self.metadata_at)
            if n.get('_native_wrap'):
                out[name]['conversion']='ETH_WETH_EXPLICIT_PAPER'
                out[name]['conversion_gas_units']=30000
        return out

    @staticmethod
    def received(net,amount):
        amount=D(amount)
        if amount<D(net['deposit_min']):raise EvidenceError('Bybit: депозит меньше минимума',observed=str(amount),threshold=net['deposit_min'],unit='BASE')
        fee=amount*D(net['deposit_fee']) if net['deposit_percentage'] is True else D(net['deposit_fee'])
        if fee>=amount:raise ValueError('Bybit: депозит меньше комиссии')
        return amount-fee

    @staticmethod
    def withdrawn(net,amount):
        qty=common_quantity(D(amount),[D(net['step'])])
        maximum=net.get('withdraw_max')
        if maximum not in (None,'-1',-1) and qty>D(maximum):raise EvidenceError('Bybit: превышен максимум вывода',observed=str(qty),threshold=maximum,unit='BASE')
        if qty<D(net['withdraw_min']) or qty<=D(net['fee']):
            raise EvidenceError('Bybit: вывод меньше минимума/комиссии',observed=str(qty),minimum=net['withdraw_min'],fee=net['fee'],unit='BASE')
        return qty-D(net['fee']),D(amount)-qty

    async def book(self,r):
        symbol=r['spot_symbol'];c=self.feed.accounts.client(self.venue)
        market=(self.feed.clients.get(self.venue).markets.get(symbol,{}) if self.feed.clients.get(self.venue) else {})
        if market.get('spot') is not True or market.get('active') is not True or market.get('contract') or market.get('base')!=r['base'] or market.get('quote')!='USDT':
            raise ValueError('Bybit: spot-рынок недоступен')
        if c is None or c.has.get('fetchTradingFee') is not True:raise ValueError('Bybit: нет account-read spot комиссии')
        b=await self.feed.book(self.venue,symbol)
        if b.get('fee_evidence')!='ACCOUNT_READ_ONLY':raise ValueError(self.venue.upper()+': нет account-read spot комиссии')
        return b

    async def price(self,r,sell_asset,quantity):
        decimals=r['asset_decimals'] if sell_asset else r['stable_decimals']
        raw=amount_raw(quantity,decimals)
        if raw<=0:raise ValueError('DEX: размер ниже точности токена')
        return await self.quote.price(r,sell_asset,raw)

    async def gas(self,r,q,transfer=False,swap=False,conversion=False):
        market=self.feed.clients[self.venue].markets.get(r['native_spot'],{})
        if market.get('spot') is not True or market.get('active') is not True or market.get('contract'):
            raise ValueError('Bybit: нет активного spot native/USDT для gas')
        b=await self.feed.book(self.venue,r['native_spot'])
        price=D(b['asks'][0][0]);raw=D(q.get('gas_price_raw'))
        if raw<=0 or not 0<=self.clock()-float(q['ts'])<=15 or not ZERO<=D(self.clock())-b['ts']<=3:raise ValueError('DEX: нет свежего gas/native price')
        # Declared PAPER gas-unit assumptions, not eth_estimateGas or receipts.
        units=(200000 if transfer else 0)+(100000 if swap else 0)+(30000 if conversion else 0)
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
        b=await self.book(r);client=self.feed.clients[self.venue];m=client.markets[r['spot_symbol']]
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
        if sold>0:limits(self.feed.clients[self.venue].markets[r['spot_symbol']],sold,cash)
        return plain(dict(sold=sold,cash=cash-fee,fee=fee,dust=quantity-qty,gas=0,ts=b['ts'],quote=None))

    async def transfer(self,r,asset,src,dst,amount,networks,q=None):
        amount=D(amount);gas=ZERO;dust=ZERO
        if src==dst:return plain(dict(amount=amount,gas=0,dust=0,fee=0))
        if not dex(src) and not dex(dst):
            if asset!='USDT':raise ValueError('DEX_TRANSFER_EXTERNAL_CEX_ASSET_UNSUPPORTED')
            network=await self.feed.quote_route(src,dst)
            if network.get('deposit_fee') is None or network.get('deposit_fee_percentage') is not False or D(network['deposit_fee'])!=0:
                raise ValueError('Не подтверждена комиссия CEX-депозита USDT')
            fee=D(network['fee']);received=amount-fee
            if amount<D(network['withdraw_minimum']) or received<D(network['deposit_minimum']) or received<=0:
                raise ValueError('USDT меньше комиссии или минимумов предварительного перевода')
            return plain(dict(amount=received,gross=amount,gas=0,dust=0,fee=fee,cex_route=network,
                              source=src,destination=dst,delay_seconds=network.get('delay_seconds','900')))
        peer=next((v for v in (src,dst) if not dex(v) and v!=self.venue),None)
        if peer:
            adapter=self.external_sources.setdefault(peer,Source(self.feed,self.paired,peer))
            required={r['base']:set(),'USDT':set()}
            required[asset]={'withdraw' if src==peer else 'deposit'}
            evidence=await adapter.networks(r,required)
            return dict(await adapter.transfer(r,asset,src,dst,amount,evidence,q),external_venue=peer)
        conversion=None
        if networks[asset].get('conversion')=='ETH_WETH_EXPLICIT_PAPER':
            q=q or await self.gas_probe(r)
            gas=await self.gas(r,q,conversion=True)
            conversion=dict(operation='WRAP_ETH_TO_WETH' if dex(dst) else 'UNWRAP_WETH_TO_ETH',
                            amount=str(amount),gas_units=30000,gas_evidence='UNMEASURED_PAPER_UNITS',
                            execution_verified=False,live_allowed=False)
        if src==self.venue:
            received,dust=self.withdrawn(networks[asset],amount);fee=amount-received-dust
        else:
            q=q or await self.gas_probe(r)
            gas+=await self.gas(r,q,transfer=True)
            received=self.received(networks[asset],amount);fee=amount-received
        if conversion and src==self.venue:conversion['amount']=str(received)
        return plain(dict(amount=received,gross=amount if src!=self.venue else received,gas=gas,dust=dust,fee=fee,conversion=conversion))

    async def receipt(self,r,asset,transfer,destination,networks):
        if transfer.get('cex_route'):
            fresh=await self.feed.quote_route(transfer['source'],transfer['destination'])
            if fresh['network']!=transfer['cex_route']['network']:
                raise ValueError('Сеть предварительного CEX-перевода изменилась')
            if fresh.get('deposit_fee') is None or fresh.get('deposit_fee_percentage') is not False or D(fresh['deposit_fee'])!=0:
                raise ValueError('Не подтверждена комиссия зачисления USDT')
            return D(transfer['amount'])
        if dex(destination):return D(transfer['amount'])
        if destination==self.venue:return self.received(networks[asset],transfer['gross'])
        adapter=self.external_sources.setdefault(destination,Source(self.feed,self.paired,destination))
        evidence=await adapter.networks(r,{r['base']:set(),'USDT':{'deposit'}})
        return adapter.received(evidence[asset],transfer['gross'])

    async def review(self,symbol,buy,sell,read_accounts=False,route=None,reserved=None):
        from .decision_journal import failure_stage
        journal=getattr(self.feed,'journal',None)
        if journal is None:return await self._review(symbol,buy,sell,read_accounts,route,reserved)
        control=await self.store.control()
        trace=await journal.start('B',symbol,buy,sell,'observe' if control['mode']=='observe' else 'paper_'+control['mode'],
                                  source=(route or {}).get('signal_source','scanner'),evidence=dict(kind=KIND,settings=control))
        from .preflight_progress import current, snapshot
        progress_token=current.set(dict(stage='TRANSFER_NETWORKS'))
        try:
            result=await self._review(symbol,buy,sell,read_accounts,route,reserved)
            if 'Нужны новые цены входа' in result['reasons']:
                await journal.check(trace,'QUOTES','WAIT','REFRESH_AFTER_SLOW_PREFLIGHT',
                                    dict(economics=result['transfer']['economics'],maximum_age_seconds=3))
                result=await self._review(symbol,buy,sell,read_accounts,route,reserved)
            result['trace_id']=trace;result['transfer']['trace_id']=trace
            failed_stages={failure_stage(reason) for reason in result['reasons']}
            for stage in ('MATCH','IDENTITY','NETWORK','QUOTES'):
                await journal.check(trace,stage,'FAIL' if stage in failed_stages else 'PASS','DEX_SPOT_TRANSFER_PREFLIGHT',dict(route=result['transfer']['route']))
            await journal.check(trace,'COSTS','FAIL' if 'COSTS' in failed_stages else 'PASS','DEX_TRANSFER_ECONOMICS',dict(economics=result['transfer']['economics'],thresholds=control))
            if not result['reasons']:await journal.check(trace,'RISK','PASS','DEX_TRANSFER_CAPITAL_CLEAR')
            for reason in result['reasons']:
                await journal.check(trace,failure_stage(reason),'FAIL',reason,
                    dict(economics=result['transfer']['economics'],thresholds=control,
                         observed=result['transfer']['checks'].get(reason)))
            await journal.finish(trace,'REJECTED' if result['reasons'] else ('OBSERVED' if control['mode']=='observe' else 'READY'),
                                  result['reasons'][0] if result['reasons'] else 'PREFLIGHT_OK')
            return result
        except asyncio.CancelledError:
            await journal.finish(trace,'REJECTED','CHECK_CANCELLED',dict(stage='DEX_TRANSFER',progress=snapshot()))
            raise
        except Exception as exc:
            reason=str(exc).replace('Bybit',self.venue.upper()) if isinstance(exc,ValueError) else type(exc).__name__
            details=dict(getattr(exc,'diagnostics',{}),error_type=type(exc).__name__)
            await journal.finish(trace,'REJECTED',reason,details)
            if isinstance(exc,ValueError):raise EvidenceError(reason,**details) from None
            raise
        finally:
            current.reset(progress_token)

    async def _review(self,symbol,buy,sell,read_accounts=False,route=None,reserved=None):
        r=copy.deepcopy(route or self.route_for(symbol,buy,sell))
        control=await self.store.control()
        if self.venue not in self.feed.trade_venues:raise ValueError('Подключи '+self.venue.upper()+' spot API чтения')
        if not self.feed.venue_allowed(self.venue,'paper'):raise ValueError('Площадка выключена для новых PAPER-входов')
        budget=D(control['budget']);state=self.paper.state
        if not reserved and (D(state['realized'])<=-D(control['total_loss']) or D(state['daily'].get(day(self.clock()),0))<=-D(control['daily_loss'])):
            raise ValueError('Достигнут лимит убытка PAPER')
        source=reserved['source'] if reserved else self.paper.funding_source(buy,budget)
        if source not in ('bybit','mexc','dex'+str(r['chain_id'])):raise ValueError('Нет исходного USDT для этого маршрута')
        networks=await self.networks(r,self.required_operations(r['base'],source,buy,sell))
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
        self.last_economics=dict(symbol=symbol,buy=buy,sell=sell,spread_pct=float(spread),net=float(net),stress_net=float(stress),ts=self.clock())
        reserve=max(D('.10'),gas*2)
        if not reserved and D(state['balances'].get(source,'0'))<budget+reserve:reasons.append('Не хватает виртуального USDT с резервом gas')
        economics=plain(dict(budget=budget,purchase_spent=entry['cost'],buy_fee=entry['buy_fee'],
            sale_proceeds=sale['cash'],sell_fee=sale['fee'],returned=returned['amount'],
            funding_fee=funding['fee'],base_transfer_fee=base['fee'],return_fee=returned['fee'],
            gas=gas,leftover=leftover,spent=spent,net=net,net_after_reserve=net-D('.10'),
            safety=D('.10'),stress_drop_pct=control['stress_drop'],stress_loss=D(sale['cash'])*D(control['stress_drop'])/100,
            stress_net=stress,spread_pct=spread,risk_evidence='OPERATOR_PAPER_SCENARIO_NOT_A_FEE'))
        checks=plain({
            'Спред вне заданных границ':dict(value=spread,minimum=control['min_spread'],maximum=control['max_spread'],unit='percent'),
            'Стресс-NET после переводов и gas ниже минимума':dict(value=stress,minimum=control['min_net'],unit='USD'),
            'Недостаточно глубины предполагаемой продажи':dict(sold=sale['sold'],dust=sale['dust'],required=base['amount'],unit='BASE'),
            'Нужны новые цены входа':dict(buy_age_seconds=self.clock()-float(entry['ts']),sell_age_seconds=self.clock()-float(sale['ts']),maximum_seconds=3),
            'Не хватает виртуального USDT с резервом gas':dict(value=state['balances'].get(source,'0'),minimum=budget+reserve,unit='USD')})
        p=plain(dict(kind=KIND,route=r,source=source,budget=budget,gas_reserve=reserve,
                     buy_qty=entry['qty'],received_qty=base['amount'],sell_qty=sale['sold'],spent=entry['cost'],
                     buy_fee=entry['buy_fee'],sell_fee=sale['fee'],buy_vwap=buy_vwap,sell_vwap=sell_vwap,
                     base_route=dict(network=str(r['chain_id']),fee=base['fee']),return_fee=returned['fee'],
                     spread_pct=spread,net_after_reserve=net-D('.10'),stress_net=stress,reasons=reasons,
                     preview_buy=entry,preview_base=base,economics=economics,checks=checks,
                     costs=dict(gas=gas,funding=funding,base=base,returned=returned),network_evidence=networks,
                     delay_evidence='UNMEASURED_ETH_120S_BSC_8S_PLUS_'+self.venue.upper()+'_300S_PER_TRANSFER_PAPER',gas_evidence='200K_TRANSFER_100K_APPROVAL_PAPER_UNITS'))
        return dict(symbol=symbol,buy=buy,sell=sell,ts=min(float(entry['ts']),float(sale['ts'])),settings_revision=control['revision'],
                    spread_pct=str(spread),reasons=reasons,transfer=p,trade_configured=True,mode=control['mode'])

    def required_operations(self,base,source,buy,sell):
        required={'USDT':set(),base:set()}
        for asset,src,dst in (('USDT',source,buy),(base,buy,sell),('USDT',sell,source)):
            if src==dst:continue
            if src==self.venue:required[asset].add('withdraw')
            if dst==self.venue:required[asset].add('deposit')
        return required

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
