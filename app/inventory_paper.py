"""Durable isolated PAPER wallet. No private clients, orders or withdrawals.

Orders use displayed native-lot depth and IOC limits. A later second-leg book
can cause a partial fill and a loss on recovery. Transfer receipts remain an
explicit time/status model, never evidence of real deposits or live readiness.
"""
import asyncio
import copy
import json
import time
from datetime import datetime, timezone
import aiosqlite
from .strategy_math import D, ZERO, ONE, Invalid, positive, nonnegative, plain, common_quantity

SCHEMA='''
CREATE TABLE IF NOT EXISTS inventory_paper_state(id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS inventory_paper_trades(id INTEGER PRIMARY KEY,ts REAL,status TEXT,net REAL,payload TEXT);
CREATE TABLE IF NOT EXISTS inventory_paper_events(id INTEGER PRIMARY KEY,ts REAL,reason TEXT,payload TEXT);
'''
DEFAULTS=dict(capital='250',max_notional='25',min_spread_pct='5',max_spread_pct='10',
              min_net='0.25',safety='0.10',max_age='3',max_skew='1',daily_loss='1.25',
              portfolio_loss='2.5',max_pending_seconds='3600',max_unhedged_seconds='10',cooldown='300',ioc_slippage='0.001')


def policy(values=None):
    if set(values or {})-set(DEFAULTS):
        raise Invalid('INVENTORY_SETTING_UNKNOWN')
    p={k:nonnegative(v) for k,v in dict(DEFAULTS,**(values or {})).items()}
    if (any(p[k]<=0 for k in p if k not in ('safety','cooldown'))
            or not ONE<=p['min_spread_pct']<=p['max_spread_pct']<=50
            or p['max_age']>10 or p['max_skew']>p['max_age'] or p['ioc_slippage']>D('.01')):
        raise Invalid('INVENTORY_POLICY_INVALID')
    return p


def day(ts):
    return datetime.fromtimestamp(float(D(ts)+D(18000)),timezone.utc).date().isoformat()


def fill(book,qty,side,limit=None):
    """Partial native BASE fill up to an IOC limit, using visible depth only."""
    remaining=positive(qty);levels=book['asks' if side=='buy' else 'bids'];cash=ZERO;done=ZERO
    step=positive(book['step'])
    for price,size in levels:
        price,size=positive(price),positive(size)
        if limit is not None and ((side=='buy' and price>limit) or (side=='sell' and price<limit)):
            break
        q=common_quantity(min(remaining,size),[step])
        if q<=0:
            continue
        done+=q;cash+=q*price;remaining-=q
        if remaining<=0:
            break
    return done,cash,cash*nonnegative(book['fee_rate'])


def explanation(plan):
    return ('Доход: продажа заранее размещённого BASE дороже покупки такого же объёма на другой бирже. '
            'Сжатия спреда не ждём. После исполнения — восстановление запасов переводами. '
            f"Оценка NET: {D(plan['projected_net']):+.4f} USDT; резерв {D(plan['safety']):.4f} отдельно. "
            'Частичный fill: продать купленный непокрытый остаток; убыток сохранить. '
            'Завершение: остаток устранён и оба перевода зачислены по модели. '
            'Остановка сети блокирует новые входы. Запас монет несёт рыночный риск. '
            'PAPER: видимая глубина не гарантирует реальные fills; срок перевода — допущение.')


class Engine:
    def __init__(self,path,values=None,clock=time.time):
        self.path,self.p,self.clock=str(path),policy(values),clock
        self.state=None;self.version=0;self.lock=asyncio.Lock();self.on_event=None

    @property
    def positions(self):
        return (self.state or {}).get('positions',{})

    async def init(self):
        initial=dict(initialized=False,balances={},baseline={},positions={},realized='0',closed=0,
                     wins=0,losses=0,fees='0',transfer_costs='0',last_entry={},daily={},halted='',
                     last_ts='0',last_reason='WAITING_FOR_SEED_QUOTES',notifications={},policy=plain(self.p))
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            await db.execute('INSERT OR IGNORE INTO inventory_paper_state VALUES(1,0,?)',(json.dumps(initial),))
            async with db.execute('SELECT version,payload FROM inventory_paper_state WHERE id=1') as c:
                self.version,payload=await c.fetchone()
            self.state=json.loads(payload);await db.commit()
        self.p=policy(self.state['policy'])

    async def save(self,state,reason,payload,trades=()):
        now=D(self.clock())
        if now<D(state['last_ts']):
            raise Invalid('INVENTORY_CLOCK_REVERSED')
        state.update(last_ts=str(now),last_reason=reason)
        async with aiosqlite.connect(self.path) as db:
            await db.execute('BEGIN IMMEDIATE')
            c=await db.execute('UPDATE inventory_paper_state SET version=version+1,payload=? WHERE id=1 AND version=?',
                               (json.dumps(plain(state),allow_nan=False),self.version))
            if c.rowcount!=1:
                raise Invalid('INVENTORY_CONCURRENT_WRITER')
            await db.execute('INSERT INTO inventory_paper_events(ts,reason,payload) VALUES(?,?,?)',
                             (float(now),reason,json.dumps(plain(payload),allow_nan=False)))
            for p in trades:
                await db.execute('INSERT INTO inventory_paper_trades VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,net=excluded.net,payload=excluded.payload',
                    (p['id'],float(p['opened_at']),p['status'],float(p['net']) if p.get('net') is not None else None,json.dumps(plain(p))))
            await db.commit()
        self.state,self.version=plain(state),self.version+1

    async def note(self,reason,payload):
        async with self.lock:
            await self.save(copy.deepcopy(self.state),reason,payload)

    async def configure(self,**changes):
        if set(changes)-{'min_spread_pct','max_notional','min_net'}:
            raise Invalid('INVENTORY_SETTING_UNKNOWN')
        async with self.lock:
            new=policy(dict(plain(self.p),**changes));state=copy.deepcopy(self.state)
            state['policy']=plain(new)
            await self.save(state,'INVENTORY_POLICY_CHANGED',dict(before=plain(self.p),after=plain(new)))
            self.p=new

    def fresh(self,snapshot):
        now=D(self.clock())
        if snapshot.get('identity_verified') is not True:
            raise Invalid('INVENTORY_IDENTITY_UNKNOWN')
        if not ZERO<=now-D(snapshot['ts'])<=self.p['max_age']:
            raise Invalid('INVENTORY_QUOTE_STALE')
        if not ZERO<=D(snapshot['skew'])<=self.p['max_skew']:
            raise Invalid('INVENTORY_QUOTES_NOT_SYNCHRONIZED')
        for b in snapshot['books']:
            self.fresh_book(b)

    def fresh_book(self,book):
        if not ZERO<=D(self.clock())-D(book['ts'])<=self.p['max_age']:
            raise Invalid('INVENTORY_QUOTE_STALE')

    async def seed(self,snapshots):
        async with self.lock:
            if self.state['initialized'] or not snapshots:
                return
            books={}
            for s in snapshots:
                self.fresh(s);books.update(s['wallet_books'])
            venues={k.split('|')[0] for k in books}
            if len(venues)!=2:
                raise Invalid('INVENTORY_SEED_TWO_VENUES_REQUIRED')
            balances={v+'|USDT':str(self.p['capital']/2/len(venues)) for v in venues}
            allocation=self.p['capital']/2/len(books)
            for key,b in books.items():
                balances[key]=str(allocation/positive(b['bid']))
            state=copy.deepcopy(self.state)
            state.update(initialized=True,balances=balances,baseline=copy.deepcopy(balances),
                         initial_equity=str(self.p['capital']),seeded_at=str(self.clock()),
                         seed_evidence='ONE_VIRTUAL_ALLOCATION_AT_EXECUTABLE_BIDS_NOT_PROFIT')
            await self.save(state,'VIRTUAL_INVENTORY_SEEDED',dict(balances=balances))

    def plan(self,s):
        self.fresh(s);now=D(self.clock());a,b=s['books'];buy,sell,asset=s['buy'],s['sell'],s['asset']
        if buy==sell or asset=='USDT' or a['venue']!=buy or b['venue']!=sell or a['asset']!=asset or b['asset']!=asset:
            raise Invalid('INVENTORY_ROUTE_IDENTITY_MISMATCH')
        if not self.state['initialized']:
            raise Invalid('INVENTORY_NOT_SEEDED')
        if self.state['halted']:
            raise Invalid(self.state['halted'])
        if self.state.get('valuation_error'):
            raise Invalid('INVENTORY_VALUATION_UNAVAILABLE')
        if self.positions:
            raise Invalid('INVENTORY_REBALANCE_OR_RECOVERY_PENDING')
        if D(self.state['daily'].get(day(now),0))<=-self.p['daily_loss']:
            raise Invalid('INVENTORY_DAILY_STOP')
        route=asset+'|'+buy+'|'+sell
        if now-D(self.state['last_entry'].get(route,'-99999999999'))<self.p['cooldown']:
            raise Invalid('INVENTORY_COOLDOWN')
        rb=s['rebalance']
        if rb.get('verified') is not True:
            raise Invalid(rb.get('reason','INVENTORY_NETWORK_UNKNOWN'))
        if not ZERO<=now-D(rb['ts'])<=300:
            raise Invalid('INVENTORY_NETWORK_STALE')
        for name in ('base','quote'):
            if rb[name].get('available') is not True:
                raise Invalid('INVENTORY_NETWORK_UNAVAILABLE')
            positive(rb[name]['delay_seconds']);nonnegative(rb[name]['fee']);nonnegative(rb[name]['minimum'])
        cash=D(self.state['balances'].get(buy+'|USDT',0));inventory=D(self.state['balances'].get(sell+'|'+asset,0))
        requested=min(self.p['max_notional']/positive(a['ask']),cash/(positive(a['ask'])*(ONE+nonnegative(a['fee_rate']))),inventory)
        if requested<=0:
            raise Invalid('INVENTORY_NO_BALANCE')
        qty=common_quantity(requested,[a['step'],b['step']])
        if qty<=0:
            raise Invalid('INVENTORY_COMMON_LOT_ZERO')
        qa,cost,fa=fill(a,qty,'buy');qb,proceeds,fb=fill(b,qty,'sell')
        if qa!=qty or qb!=qty:
            raise Invalid('INVENTORY_DEPTH_LOW')
        if cost>self.p['max_notional'] or cost+fa>cash:
            raise Invalid('INVENTORY_NOTIONAL_OR_CASH_LIMIT')
        for book,amount in ((a,cost),(b,proceeds)):
            if qty<nonnegative(book['min_qty']) or amount<nonnegative(book['min_notional']):
                raise Invalid('INVENTORY_NATIVE_MINIMUM')
        spread=(proceeds/cost-ONE)*100
        if spread<self.p['min_spread_pct']:
            raise Invalid('INVENTORY_SPREAD_TOO_SMALL')
        if spread>self.p['max_spread_pct']:
            raise Invalid('INVENTORY_SPREAD_ANOMALY')
        base_fee,quote_fee=D(rb['base']['fee']),D(rb['quote']['fee'])
        if qty<D(rb['base']['minimum']) or cost+fa<D(rb['quote']['minimum']):
            raise Invalid('INVENTORY_TRANSFER_MINIMUM')
        if D(self.state['balances'].get(buy+'|'+asset,0))<base_fee:
            raise Invalid('INVENTORY_BASE_FEE_RESERVE_LOW')
        if D(self.state['balances'].get(sell+'|USDT',0))+proceeds-fb<cost+fa+quote_fee:
            raise Invalid('INVENTORY_QUOTE_FEE_RESERVE_LOW')
        net=proceeds-cost-fa-fb-base_fee*cost/qty-quote_fee
        if net-self.p['safety']<self.p['min_net']:
            raise Invalid('INVENTORY_NET_TOO_SMALL')
        return plain(dict(route=route,asset=asset,buy=buy,sell=sell,quantity=qty,cost=cost,proceeds=proceeds,
            ask=cost/qty,bid=proceeds/qty,spread_pct=spread,projected_net=net,safety=self.p['safety'],
            rebalance=rb,ioc_slippage=self.p['ioc_slippage'],quality='PUBLIC_DEPTH_PAPER_NOT_REAL_FILLS'))

    @staticmethod
    def change(state,key,amount):
        result=D(state['balances'].get(key,0))+amount
        if result < -D('1e-24'):
            raise Invalid('INVENTORY_BALANCE_UNDERFLOW')
        state['balances'][key]=str(max(ZERO,result))

    def finish(self,state,pos,reason):
        net=D(pos['cash_result'])-D(pos.get('base_fee_value',0))
        pos.update(status='CLOSED',closed_at=str(self.clock()),net=str(net),close_reason=reason)
        state['positions'].pop(str(pos['id']),None)
        state['realized']=str(D(state['realized'])+net);state['closed']+=1
        state['win_total']=str(D(state.get('win_total',0))+max(ZERO,net))
        state['loss_total']=str(D(state.get('loss_total',0))+min(ZERO,net))
        state['peak_realized']=str(max(D(state.get('peak_realized',0)),D(state['realized'])))
        state['drawdown_realized']=str(max(D(state.get('drawdown_realized',0)),D(state['peak_realized'])-D(state['realized'])))
        state['wins' if net>0 else 'losses']+=int(net!=0)
        d=day(self.clock());state['daily'][d]=str(D(state['daily'].get(d,0))+net)

    def transfers(self,state,pos):
        p=pos['plan'];q=D(pos['matched']);rb=p['rebalance']
        if q==0:
            self.finish(state,pos,'UNHEDGED_MODEL_UNWIND');return
        restore=D(pos['matched_buy_cash']);basefee=D(rb['base']['fee']);quotefee=D(rb['quote']['fee'])
        if q<D(rb['base']['minimum']) or restore<D(rb['quote']['minimum']):
            pos['status']='RECOVERY_REQUIRED';pos['recovery_reason']='PARTIAL_BELOW_TRANSFER_MINIMUM';return
        if D(state['balances'].get(p['buy']+'|'+p['asset'],0))<q+basefee or D(state['balances'].get(p['sell']+'|USDT',0))<restore+quotefee:
            pos['status']='RECOVERY_REQUIRED';pos['recovery_reason']='TRANSFER_BALANCE_SHORTFALL';return
        pos['transfers']=[]
        for name,asset,amount,fee,src,dst in (
            ('base',p['asset'],q,basefee,p['buy'],p['sell']),
            ('quote','USDT',restore,quotefee,p['sell'],p['buy'])):
            self.change(state,src+'|'+asset,-amount-fee)
            pos['transfers'].append(dict(kind=name,asset=asset,amount=str(amount),fee=str(fee),source=src,destination=dst,
                network=rb[name]['network'],status='IN_TRANSIT',due=str(D(self.clock())+D(rb[name]['delay_seconds']))))
        pos['cash_result']=str(D(pos['cash_result'])-quotefee)
        pos['base_fee_value']=str(basefee*D(pos['buy_price']))
        state['transfer_costs']=str(D(state['transfer_costs'])+quotefee+D(pos['base_fee_value']))
        pos['status']='REBALANCE_PENDING'

    async def execute(self,snapshot,first,second,recovery,allow_entry=True):
        """Initial plan + sequential book captures, never fictitious zero-loss fills."""
        async with self.lock:
            if not allow_entry:
                return None
            # All plan gates again; recovery data cannot authorize new positions.
            plan=self.plan(snapshot)
            for b,venue in ((first,plan['buy']),(second,plan['sell']),(recovery,plan['buy'])):
                self.fresh_book(b)
                if b['venue']!=venue or b['asset']!=plan['asset']:
                    raise Invalid('INVENTORY_EXECUTION_BOOK_IDENTITY_MISMATCH')
            if not D(first['ts'])<=D(second['ts'])<=D(recovery['ts']):
                raise Invalid('INVENTORY_EXECUTION_TIME_REVERSED')
            requested=D(plan['quantity']);slip=D(plan['ioc_slippage'])
            q,cost,fee=fill(first,requested,'buy',D(plan['ask'])*(ONE+slip))
            q=common_quantity(q,[first['step'],second['step']]) if q>0 else ZERO
            if q==0:
                await self.save(copy.deepcopy(self.state),'INVENTORY_BUY_IOC_UNFILLED',plan);return None
            q,cost,fee=fill(first,q,'buy',D(plan['ask'])*(ONE+slip))
            if cost>self.p['max_notional'] or cost+fee>D(self.state['balances'].get(plan['buy']+'|USDT',0)):
                raise Invalid('INVENTORY_EXECUTION_NOTIONAL_LIMIT')
            sold,proceeds,sellfee=fill(second,q,'sell',D(plan['bid'])*(ONE-slip))
            state=copy.deepcopy(self.state);change=lambda key,amount:self.change(state,key,amount)
            change(plan['buy']+'|USDT',-cost-fee);change(plan['buy']+'|'+plan['asset'],q)
            change(plan['sell']+'|'+plan['asset'],-sold);change(plan['sell']+'|USDT',proceeds-sellfee)
            pos=dict(id=self.version+1,plan=plan,opened_at=str(self.clock()),status='RECOVERY_REQUIRED',net=None,
                bought=str(q),sold=str(sold),matched=str(sold),residual=str(q-sold),buy_price=str(cost/q),
                matched_buy_cash=str((cost+fee)*sold/q),cash_result=str(proceeds-cost-fee-sellfee),
                fills=[dict(side='buy',venue=plan['buy'],quantity=str(q),cash=str(cost),fee=str(fee),ts=first['ts']),
                       dict(side='sell',venue=plan['sell'],quantity=str(sold),cash=str(proceeds),fee=str(sellfee),ts=second['ts'])],
                transfers=[],thesis=explanation(plan))
            state['fees']=str(D(state['fees'])+fee+sellfee)
            self.recover(state,pos,recovery)
            if D(pos['residual'])==0:
                self.transfers(state,pos)
            if pos['status']!='CLOSED':
                state['positions'][str(pos['id'])]=pos
            state['last_entry'][plan['route']]=str(self.clock())
            self.enqueue(state,'OPEN',pos)
            if pos['status']=='CLOSED':
                self.enqueue(state,'CLOSE',pos)
            await self.save(state,'INVENTORY_EXECUTION_MODEL',pos,[pos])
        await self.flush_notifications()
        return pos

    def recover(self,state,pos,book):
        residual=D(pos['residual'])
        if residual<=0:
            return
        q,cash,fee=fill(book,residual,'sell')
        if q:
            p=pos['plan'];self.change(state,p['buy']+'|'+p['asset'],-q)
            self.change(state,p['buy']+'|USDT',cash-fee)
            pos['cash_result']=str(D(pos['cash_result'])+cash-fee);pos['residual']=str(residual-q)
            pos['fills'].append(dict(side='recovery_sell',venue=p['buy'],quantity=str(q),cash=str(cash),fee=str(fee),ts=book['ts']))
            state['fees']=str(D(state['fees'])+fee)

    def reverse_matched(self,state,pos,a,b):
        # A tiny matched fill may be below withdrawal minima. Unwind BOTH
        # spot changes at current prices rather than waiting for convergence.
        q=D(pos['matched'])
        qa,ca,fa=fill(a,q,'sell');qb,cb,fb=fill(b,q,'buy')
        if qa!=q or qb!=q or D(state['balances'].get(pos['plan']['sell']+'|USDT',0))<cb+fb:
            raise Invalid('INVENTORY_MATCHED_UNWIND_DEPTH_OR_CASH_LOW')
        p=pos['plan']
        self.change(state,p['buy']+'|'+p['asset'],-q);self.change(state,p['buy']+'|USDT',ca-fa)
        self.change(state,p['sell']+'|'+p['asset'],q);self.change(state,p['sell']+'|USDT',-cb-fb)
        pos['cash_result']=str(D(pos['cash_result'])+ca-fa-cb-fb)
        pos['fills'].extend([dict(side='matched_unwind_sell',venue=p['buy'],quantity=str(q),cash=str(ca),fee=str(fa),ts=a['ts']),
                             dict(side='matched_unwind_buy',venue=p['sell'],quantity=str(q),cash=str(cb),fee=str(fb),ts=b['ts'])])
        state['fees']=str(D(state['fees'])+fa+fb)
        pos['matched']='0';self.finish(state,pos,'PARTIAL_TRANSFER_INFEASIBLE_UNWIND')

    async def monitor(self,source):
        async with self.lock:
            state=copy.deepcopy(self.state);changed=[];events=[];now=D(self.clock())
            for key,pos in list(state['positions'].items()):
                if pos['status']=='RECOVERY_REQUIRED':
                    try:
                        book=await source.book(pos['plan']['buy'],pos['plan']['asset']);self.fresh_book(book)
                        self.recover(state,pos,book)
                        if D(pos['residual'])==0:
                            if pos.get('recovery_reason') in ('PARTIAL_BELOW_TRANSFER_MINIMUM','TRANSFER_BALANCE_SHORTFALL'):
                                other=await source.book(pos['plan']['sell'],pos['plan']['asset']);self.fresh_book(other)
                                self.reverse_matched(state,pos,book,other)
                            else:
                                self.transfers(state,pos)
                    except Exception as exc:
                        pos['data_error']=type(exc).__name__
                for t in pos['transfers']:
                    if t['status']=='CREDITED' or now<D(t['due']):
                        continue
                    try:
                        available=await source.network_check(pos['plan'],t)
                    except Exception:
                        available=False
                    if available is True:
                        self.change(state,t['destination']+'|'+t['asset'],D(t['amount']))
                        t.update(status='CREDITED',credited_at=str(now))
                if pos['status']!='CLOSED' and pos['transfers'] and all(t['status']=='CREDITED' for t in pos['transfers']):
                    self.finish(state,pos,'REBALANCE_MODEL_COMPLETE')
                if pos['status']=='CLOSED':
                    events.append(('CLOSE',pos))
                elif D(pos['residual'])>0 and now-D(pos['opened_at'])>=self.p['max_unhedged_seconds']:
                    if not state['halted']:
                        state['halted']='INVENTORY_UNHEDGED_TIMEOUT';events.append(('ALERT',pos))
                elif now-D(pos['opened_at'])>=self.p['max_pending_seconds']:
                    pos['status']='REBALANCE_DELAYED' if pos['transfers'] else 'RECOVERY_REQUIRED'
                    if not state['halted']:
                        state['halted']='INVENTORY_TRANSFER_OR_RECOVERY_TIMEOUT';events.append(('ALERT',pos))
                changed.append(pos)
            if changed:
                for event,pos in events:
                    self.enqueue(state,event,pos)
                await self.save(state,'INVENTORY_MONITOR',dict(positions=changed),changed)
        await self.flush_notifications()

    async def mark(self,books):
        async with self.lock:
            if not self.state['initialized']:
                return
            state=copy.deepcopy(self.state);balances={k:D(v) for k,v in state['balances'].items()}
            for pos in state['positions'].values():
                for t in pos['transfers']:
                    if t['status']!='CREDITED':
                        key=t['source']+'|'+t['asset'];balances[key]=balances.get(key,ZERO)+D(t['amount'])
            def value(wallet):
                total=ZERO
                for key,qty in wallet.items():
                    if key.endswith('|USDT'):
                        total+=D(qty)
                    else:
                        b=books[key];self.fresh_book(b);total+=D(qty)*positive(b['bid'])
                return total
            try:
                equity,baseline=value(balances),value(state['baseline'])
            except (KeyError,Invalid):
                state['valuation_error']='INVENTORY_VALUATION_UNAVAILABLE'
            else:
                state.pop('valuation_error',None)
                state['valuation']=plain(dict(ts=self.clock(),equity=equity,total_pnl=equity-self.p['capital'],
                    inventory_market_pnl=baseline-self.p['capital'],excess_vs_inventory=equity-baseline))
                if equity-self.p['capital']<=-self.p['portfolio_loss']:
                    state['halted']='INVENTORY_PORTFOLIO_STOP'
            await self.save(state,'INVENTORY_PORTFOLIO_MARK',state.get('valuation',{}))

    @staticmethod
    def enqueue(state,event,pos):
        # At-least-once delivery: an ambiguous Telegram timeout may duplicate a
        # message, but cannot drop an already committed trade explanation.
        key=str(pos['id'])+':'+event
        state.setdefault('notifications',{}).setdefault(key,dict(event=event,position=copy.deepcopy(pos)))

    async def flush_notifications(self):
        if self.on_event is None:
            return
        for key,item in list(self.state.get('notifications',{}).items()):
            try:
                await self.on_event(item['event'],copy.deepcopy(item['position']))
            except Exception:
                import logging
                logging.getLogger('arbitrage').exception('Inventory notification failed; retry remains queued')
                break
            async with self.lock:
                state=copy.deepcopy(self.state);state['notifications'].pop(key,None)
                await self.save(state,'INVENTORY_NOTIFICATION_DELIVERED',dict(key=key))
