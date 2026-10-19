"""Read-only spot and network capture. Identity comes from a curated registry.

Native BTC/ETH/SOL are included. Additional tokens need matching chain+contract
in currency metadata on BOTH venues. A channel ticker is never an identity.
"""
import asyncio
import time
from .public_books import normalize
from .strategy_math import D, ZERO, ONE, Invalid, positive, nonnegative, plain

NATIVE={'BTC':('BTC','BITCOIN'),'ETH':('ETH','ERC20','ETHEREUM'),'SOL':('SOL','SOLANA')}
USDT={'TRC20':('TRC20','TRX'),'ERC20':('ETH','ERC20')}
USDT_CONTRACTS={'TRC20':'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t','ERC20':'0xdac17f958d2ee523a2206206994597c13d831ec7'}


def step(client,market):
    value=(market.get('precision') or {}).get('amount');mode=getattr(client,'precisionMode',None)
    if mode==4:
        return positive(value)
    if mode==2 and value is not None and D(value)==int(value) and 0<=int(value)<=18:
        return D(10)**(-int(value))
    raise Invalid('INVENTORY_NATIVE_PRECISION_UNKNOWN')


def same_contract(a,b,network):
    return str(a).lower()==str(b).lower() if network in ('ERC20','ETH','ETHEREUM') else a==b


class Source:
    def __init__(self,clients,venues=('bybit','gateio'),registry=None,clock=time.time,network_clients=None):
        if len(venues)!=2 or len(set(venues))!=2:
            raise Invalid('INVENTORY_TWO_VENUES_REQUIRED')
        self.venues=tuple(venues);self.clients={v:clients[v] for v in venues if v in clients}
        self.registry=registry if registry is not None else {a:dict(native=True,networks=list(n)) for a,n in NATIVE.items()}
        if not isinstance(self.registry,dict) or not 1<=len(self.registry)<=20:
            raise Invalid('INVENTORY_REGISTRY_LIMIT')
        for asset,spec in self.registry.items():
            if not asset.isalnum() or asset=='USDT' or not isinstance(spec.get('networks'),list) or not spec['networks']:
                raise Invalid('INVENTORY_REGISTRY_INVALID')
            if spec.get('native') is True:
                if asset not in NATIVE or set(spec['networks'])-set(NATIVE[asset]):
                    raise Invalid('INVENTORY_NATIVE_IDENTITY_UNKNOWN')
            elif not spec.get('contract'):
                raise Invalid('INVENTORY_TOKEN_CONTRACT_REQUIRED')
        self.network_clients=network_clients or {}
        self.clock=clock;self.networks={};self.network_ts={};self.retry_at={};self.errors={}
        self.gate=asyncio.Semaphore(2);self.allowed_venue=lambda v:True

    async def currencies(self,venue):
        now=self.clock()
        if now<self.retry_at.get(venue,0):
            return
        self.retry_at[venue]=now+240
        try:
            client=self.network_clients.get(venue) or self.clients[venue]
            if getattr(client,'has',{}).get('fetchCurrencies') is not True:
                raise Invalid('INVENTORY_PUBLIC_NETWORK_ADAPTER_UNAVAILABLE')
            rows=await asyncio.wait_for(client.fetch_currencies(),12)
            if not isinstance(rows,dict) or not rows:
                raise Invalid('INVENTORY_PUBLIC_NETWORK_DATA_EMPTY')
            fees=None
            if getattr(client,'has',{}).get('fetchDepositWithdrawFees') is True:
                try:
                    fees=await asyncio.wait_for(client.fetch_deposit_withdraw_fees(list(self.registry)+['USDT']),12)
                except Exception as exc:
                    self.errors['fees:'+venue]=type(exc).__name__
                else:
                    self.errors.pop('fees:'+venue,None)
            from .inventory_network_metadata import enrich
            self.networks[venue]=enrich(rows,fees);self.network_ts[venue]=self.clock()
            self.errors.pop('networks:'+venue,None)
        except Exception as exc:
            self.errors['networks:'+venue]=str(exc) if isinstance(exc,Invalid) else type(exc).__name__

    def currency(self,venue,asset):
        if not ZERO<=D(self.clock())-D(self.network_ts.get(venue,-1e12))<=300:
            raise Invalid('INVENTORY_NETWORK_STATUS_UNKNOWN')
        return self.networks[venue].get(asset,{})

    @staticmethod
    def networks_for(currency):
        return {str(x.get('network') or key).upper():x for key,x in (currency.get('networks') or {}).items()}

    def identity(self,venue,asset):
        spec=self.registry[asset]
        if spec.get('native') is True:
            # Identity alone is a curated fact; network availability is separate.
            return True
        nets=self.networks_for(self.currency(venue,asset))
        return any(n in nets and same_contract(nets[n].get('contract'),spec['contract'],n) for n in spec['networks'])

    def network(self,asset,src,dst):
        a,b=self.currency(src,asset),self.currency(dst,asset)
        if a.get('withdraw') is False or b.get('deposit') is False:
            raise Invalid('INVENTORY_NETWORK_UNAVAILABLE')
        an,bn=self.networks_for(a),self.networks_for(b)
        if asset=='USDT':
            groups=[(key,names,USDT_CONTRACTS[key]) for key,names in USDT.items()]
        else:
            spec=self.registry[asset]
            groups=[(spec['networks'][0],spec['networks'],spec.get('contract'))]
        found=[]
        for canonical,names,contract in groups:
            aa=next((an[n] for n in names if n in an),None);bb=next((bn[n] for n in names if n in bn),None)
            if aa is None or bb is None or aa.get('withdraw') is not True or bb.get('deposit') is not True:
                continue
            if bb.get('deposit_fee') is not None and nonnegative(bb['deposit_fee'])>0:
                continue
            if aa.get('active') is False or bb.get('active') is False:
                continue
            if contract:
                # Both token metadata entries must match the same registry asset.
                if not same_contract(aa.get('contract'),contract,canonical) or not same_contract(bb.get('contract'),contract,canonical):
                    continue
            elif aa.get('contract') or bb.get('contract'):
                continue
            if aa.get('percentage') is not False or aa.get('fee') is None:
                continue
            minimum=((aa.get('limits') or {}).get('withdraw') or {}).get('min')
            deposit=((bb.get('limits') or {}).get('deposit') or {}).get('min')
            if minimum is None or deposit is None:
                continue
            found.append(dict(network=canonical,fee=str(nonnegative(aa['fee'])),minimum=str(max(nonnegative(minimum),nonnegative(deposit))),
                              withdraw_minimum=str(nonnegative(minimum)),deposit_minimum=str(nonnegative(deposit)),
                              deposit_fee=bb.get('deposit_fee'),deposit_fee_percentage=bb.get('deposit_fee_percentage'),
                              available=True,delay_seconds='900',delay_evidence='PAPER_ASSUMPTION_NOT_MEASURED'))
        if not found:
            raise Invalid('INVENTORY_NETWORK_IDENTITY_OR_FEE_UNKNOWN')
        return min(found,key=lambda x:D(x['fee']))

    def rebalance(self,asset,buy,sell):
        try:
            return dict(verified=True,ts=min(self.network_ts.get(buy,0),self.network_ts.get(sell,0)),
                        base=self.network(asset,buy,sell),quote=self.network('USDT',sell,buy),evidence='PUBLIC_METADATA_PAPER_ONLY')
        except Invalid as exc:
            return dict(verified=False,reason=str(exc))

    async def book(self,venue,asset):
        client=self.clients[venue];symbol=asset+'/USDT';m=client.markets.get(symbol)
        if not m or m.get('spot') is not True or m.get('active') is not True or m.get('base')!=asset or m.get('quote')!='USDT':
            raise Invalid('INVENTORY_SPOT_MARKET_UNAVAILABLE')
        if not self.identity(venue,asset):
            raise Invalid('INVENTORY_TOKEN_IDENTITY_UNKNOWN')
        minimum=((m.get('limits') or {}).get('amount') or {}).get('min')
        cost=((m.get('limits') or {}).get('cost') or {}).get('min')
        if minimum is None or cost is None or m.get('taker') is None:
            raise Invalid('INVENTORY_NATIVE_MINIMUM_OR_FEE_UNKNOWN')
        async with self.gate:
            started=self.clock();raw=await asyncio.wait_for(client.fetch_order_book(symbol,limit=20),8)
            received=self.clock()
        book=normalize(raw,symbol,started,received,3,raw.get('data_source','REST'))
        if not book['asks'] or not book['bids'] or D(book['bids'][0][0])>=D(book['asks'][0][0]):
            raise Invalid('INVENTORY_CROSSED_OR_EMPTY_BOOK')
        return plain(dict(venue=venue,asset=asset,ts=D(book['timestamp'])/1000,ask=book['asks'][0][0],bid=book['bids'][0][0],
            asks=book['asks'],bids=book['bids'],step=step(client,m),min_qty=nonnegative(minimum),min_notional=nonnegative(cost),
            fee_rate=nonnegative(m['taker']),fee_evidence='PUBLIC_MARKET_FEE_PAPER_ASSUMPTION',source=book['data_source']))

    async def snapshot(self,asset,buy,sell):
        a,b=await asyncio.gather(self.book(buy,asset),self.book(sell,asset))
        return dict(asset=asset,buy=buy,sell=sell,ts=min(D(a['ts']),D(b['ts'])),skew=abs(D(a['ts'])-D(b['ts'])),
                    identity_verified=self.identity(buy,asset) and self.identity(sell,asset),books=[a,b],
                    wallet_books={buy+'|'+asset:a,sell+'|'+asset:b},rebalance=self.rebalance(asset,buy,sell))

    async def capture(self):
        await asyncio.gather(*(self.currencies(v) for v in self.clients))
        out=[]
        if len(self.clients)!=2:
            self.errors['venues']='INVENTORY_TWO_CONFIGURED_VENUES_REQUIRED';return out
        for asset in self.registry:
            try:
                a,b=self.venues;s=await self.snapshot(asset,a,b);out.append(s)
                out.append(dict(s,buy=b,sell=a,books=list(reversed(s['books'])),rebalance=self.rebalance(asset,b,a)))
                self.errors.pop(asset,None)
            except Exception as exc:
                self.errors[asset]=str(exc) if isinstance(exc,Invalid) else type(exc).__name__
        return out

    async def network_check(self,plan,transfer):
        await asyncio.gather(*(self.currencies(v) for v in (transfer['source'],transfer['destination'])))
        try:
            return self.network(transfer['asset'],transfer['source'],transfer['destination'])['network']==transfer['network']
        except Invalid:
            return False


class Service:
    def __init__(self,source,paper,allowed=lambda symbol=None:False):
        self.source,self.paper,self.allowed=source,paper,allowed
        self.entry_enabled=True;self.idle_when_disabled=False
        self.wallets={}
        self.rebalance_mode=lambda:"auto"
        self.signal_allowed=lambda:True

    @property
    def interval(self):
        return 2 if any(D(p['residual'])>0 or p['status']=='MANUAL_EXIT_PENDING' for p in self.paper.positions.values()) else 30

    async def cycle(self):
        await self.paper.monitor(self.source)
        if any(D(p['residual'])>0 or p['status']=='MANUAL_EXIT_PENDING' for p in self.paper.positions.values()):
            return []
        snapshots=await self.source.capture()
        if not self.paper.state['initialized']:
            await self.paper.seed(snapshots)
        wallets={k:b for s in snapshots for k,b in s['wallet_books'].items()}
        self.wallets=wallets
        await self.paper.mark(wallets)
        rows=[]
        for s in snapshots:
            try:
                plan=self.paper.plan(s)
                rows.append(dict(strategy='spot_inventory',symbol=s['asset']+'/USDT',buy=s['buy'],sell=s['sell'],
                    notional=float(plan['cost']),net=float(D(plan['projected_net'])/D(plan['cost'])*100),
                    hypothetical_edge=float(D(plan['projected_net'])/D(plan['cost'])*100),
                    projected_net_usd=float(plan['projected_net']),edge_unit='percent',ts=float(s['ts']),plan=plan))
                if self.entry_enabled and self.signal_allowed():
                    await self.paper.offer_candidate(plan)
                if self.entry_enabled and self.allowed(s['asset']+'/USDT') and all(self.source.allowed_venue(v) for v in (s['buy'],s['sell'])):
                    await self.enter_route(s['asset'],s['buy'],s['sell'],refresh_wallets=False)
            except (Invalid,KeyError,TypeError,ValueError,ArithmeticError) as exc:
                await self.paper.note(str(exc),dict(asset=s['asset'],buy=s['buy'],sell=s['sell'],source_errors=self.source.errors,snapshot=plain(s)))
        if not snapshots:
            await self.paper.note('INVENTORY_CAPTURE_UNAVAILABLE',self.source.errors)
        return rows

    async def enter_route(self,asset,buy,sell,refresh_wallets=True,expected_policy=None,confirmation_guard=None):
        if refresh_wallets:
            snapshots=await self.source.capture()
            self.wallets={k:b for s in snapshots for k,b in s['wallet_books'].items()}
        fresh=await self.source.snapshot(asset,buy,sell)
        await self.paper.mark(self.wallets | fresh['wallet_books'])
        self.paper.plan(fresh)
        first=await self.source.book(buy,asset)
        second=await self.source.book(sell,asset)
        recovery=await self.source.book(buy,asset)
        return await self.paper.execute(fresh,first,second,recovery,
            allow_entry=self.allowed(asset+'/USDT') and (confirmation_guard is None or confirmation_guard())
                and all(self.source.allowed_venue(v) for v in (buy,sell)),
            rebalance_mode=self.rebalance_mode(),expected_policy=expected_policy)
