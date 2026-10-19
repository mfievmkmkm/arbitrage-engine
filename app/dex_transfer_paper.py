"""Durable DEX transfer stages on the existing sequential PAPER account."""
import asyncio
import copy
import hashlib
import logging
from .dex_transfer_source import Source, KIND, dex
from .strategy_math import D, ZERO, plain
from .inventory_paper import day

CHAIN_SECONDS={1:120,56:8}  # optimistic PAPER network scenarios, not observed confirmations
BYBIT_PROCESSING_SECONDS=300  # unmeasured exchange processing scenario


def transfer_delay(chain_id):
    if chain_id not in CHAIN_SECONDS:raise ValueError('DEX_TRANSFER_DELAY_CHAIN_UNSUPPORTED')
    return CHAIN_SECONDS[chain_id]+BYBIT_PROCESSING_SECONDS


def position_delay(position):
    # A position opened before this PAPER revision keeps its 900s schedule.
    return (position.get('delay_model') or {}).get('per_transfer_seconds',900)


class Service(Source):
    enabled_name='market_signals'
    interval=3

    def __init__(self,feed,paired):
        super().__init__(feed,paired)
        self.entry_enabled=True;self.index=0;self.paused=lambda:False
        self.reasons={}
        feed.dex_transfer=self;feed.paper.dex_transfer=self

    def credit(self,state,p,venue,amount):
        amount=D(amount)
        state['balances'][venue]=str(D(state['balances'].get(venue,0))+amount)
        p['credits_earlier']=str(D(p['credits_earlier'])+amount)

    def charge(self,state,p,gas):
        gas=D(gas)
        if gas<0:raise ValueError('DEX_TRANSFER_GAS_INVALID')
        available=D(p['gas_reserved'])-D(p['gas_used'])
        if gas>available:
            extra=gas-available;src=p['source']
            if D(state['balances'].get(src,0))<extra:raise ValueError('PAPER: не хватает резерва gas; позиция сохранена')
            state['balances'][src]=str(D(state['balances'][src])-extra)
            p['cash_debited']=str(D(p['cash_debited'])+extra)
            p['gas_reserved']=str(D(p['gas_reserved'])+extra)
        p['gas_used']=str(D(p['gas_used'])+gas)

    def finish(self,state,p,proceeds,reason='CLOSED'):
        release=D(p['gas_reserved'])-D(p['gas_used'])
        returned=D(proceeds)+release
        src=p['source'];state['balances'][src]=str(D(state['balances'].get(src,0))+returned)
        net=returned+D(p['credits_earlier'])-D(p['cash_debited'])
        p.update(status=reason,closed=self.clock(),net=str(net))
        state['realized']=str(D(state['realized'])+net);state['closed']+=1
        state['wins' if net>0 else 'losses']+=1
        today=day(self.clock());state['daily'][today]=str(D(state['daily'].get(today,0))+net)
        state['last_position']=copy.deepcopy(p);state['position']=None;state['last_error']=''
        state['notifications']['close:'+p['id']]=f"🧪 PAPER #{p['id']}: цикл Bybit ↔ DEX завершён. NET модели {float(net):+.4f} USDT."

    async def open(self,review):
        async with self.paper.lock:
            e=review['signal'];plan=e['transfer']
            if plan.get('kind')!=KIND or e['reasons'] or plan['reasons'] or not e.get('trade_configured'):
                raise ValueError('DEX_TRANSFER_ENTRY_REJECTED')
            if not self.entry_enabled or not self.feed.entry_enabled or self.paused() or not await self.store.enabled():
                raise ValueError('Входы на паузе')
            if (await self.store.control())['mode']!=e['mode']:
                raise ValueError('Режим изменился; нужна новая проверка')
            if not 0<=self.clock()-e['ts']<=3:raise ValueError('Нужна новая проверка цен')
            if self.paper.state.get('position'):raise ValueError('Сначала заверши текущий PAPER-цикл')
            risk=await self.store.control()
            if D(self.paper.state['realized'])<=-D(risk['total_loss']) or D(self.paper.state['daily'].get(day(self.clock()),0))<=-D(risk['daily_loss']):
                raise ValueError('Достигнут лимит убытка PAPER')
            state=copy.deepcopy(self.paper.state);src=plan['source']
            budget=D(plan['budget']);reserve=D(plan['gas_reserve']);total=budget+reserve
            if D(state['balances'].get(src,0))<total:raise ValueError('Недостаточно виртуального USDT с резервом gas')
            p=dict(id=str(self.paper.version+1),kind=KIND,plan=copy.deepcopy(plan),
                   signal=dict(symbol=e['symbol'],buy=e['buy'],sell=e['sell']),source=src,
                   opened=self.clock(),due=self.clock(),status='BUY_PENDING',
                   original_budget=str(budget),purchase_budget=str(budget),cash_debited=str(total),
                   gas_reserved=str(reserve),gas_used='0',credits_earlier='0',funding_fee='0',
                   remaining='0',sold='0',proceeds='0',sell_fees='0',dust='0',fills=[],
                   live_allowed=False,execution_verified=False)
            p['delay_model']=dict(network_seconds=CHAIN_SECONDS[plan['route']['chain_id']],
                                  bybit_processing_seconds=BYBIT_PROCESSING_SECONDS,
                                  per_transfer_seconds=transfer_delay(plan['route']['chain_id']),
                                  source='OPERATOR_FAST_PAPER_ASSUMPTION_UNMEASURED')
            state['balances'][src]=str(D(state['balances'][src])-total)
            if src!=e['buy']:
                t=plan['costs']['funding']
                self.charge(state,p,t['gas']);self.credit(state,p,src,t['dust'])
                p.update(status='USDT_TO_BUY_IN_TRANSIT',due=self.clock()+position_delay(p),
                         purchase_budget=t['amount'],funding_fee=t['fee'],funding_transfer=t)
            state['position']=p;state['cash']=self.paper.available(state);state['last_error']=''
            await self.paper.save(state,'DEX_TRANSFER_RESERVATION',p)
            return p

    async def monitor_unlocked(self):
        state=copy.deepcopy(self.paper.state);p=state['position'];s=p['signal'];r=p['plan']['route']
        try:
            networks=await self.networks(r,self.required_operations(r['base'],p['source'],s['buy'],s['sell']))
            if p['status']=='USDT_TO_BUY_IN_TRANSIT':
                if s['buy']=='bybit':
                    receipt=self.received(networks['USDT'],p['funding_transfer']['gross'])
                    p['purchase_budget']=str(receipt)
                    p['funding_fee']=str(D(p['original_budget'])-receipt-D(p['funding_transfer']['dust']))
                p.update(status='BUY_PENDING',due=self.clock())
                # The reserved stable is now at buy; never credited as free too.
            elif p['status']=='BUY_PENDING':
                if not self.entry_enabled or not self.feed.entry_enabled or not await self.store.enabled():
                    p['due']=self.clock()+3
                    await self.paper.save(state,'DEX_TRANSFER_BUY_PAUSED',p);return
                e=await self.review(s['symbol'],s['buy'],s['sell'],route=r,reserved=p)
                if 'Нужны новые цены входа' in e['reasons']:raise ValueError('Нужны новые цены входа')
                economic=[reason for reason in e['reasons'] if reason.startswith(('Спред ', 'Стресс-NET', 'Недостаточно глубины'))]
                if economic:
                    self.credit(state,p,s['buy'],p['purchase_budget'])
                    self.finish(state,p,0,'ABORTED_AFTER_FUNDING')
                    state['last_position']['reason']=economic[0]
                    state['cash']=self.paper.available(state)
                    await self.paper.save(state,'DEX_TRANSFER_BUY_ABORTED',state['last_position']);return
                if e['reasons']:raise ValueError(e['reasons'][0])
                entry=e['transfer']['preview_buy']
                self.charge(state,p,entry['gas'])
                self.credit(state,p,s['buy'],D(p['purchase_budget'])-D(entry['cost']))
                p.update(actual_spent=entry['cost'],remaining=entry['qty'],entry_evidence=entry,
                         status='BASE_TRANSFER_PENDING',due=self.clock())
                p['fills'].append(dict(stage='BUY',ts=self.clock(),qty=entry['qty'],cash=entry['cost'],fee=entry['buy_fee']))
            elif p['status']=='BASE_TRANSFER_PENDING':
                t=await self.transfer(r,r['base'],s['buy'],s['sell'],p['remaining'],networks)
                self.charge(state,p,t['gas'])
                p.update(status='BASE_IN_TRANSIT',due=self.clock()+position_delay(p),
                         remaining=t['amount'],base_transfer=t,dust=str(D(p['dust'])+D(t['dust'])))
            elif p['status']=='BASE_IN_TRANSIT':
                if s['sell']=='bybit':p['remaining']=str(self.received(networks[r['base']],p['base_transfer']['gross']))
                p.update(status='SELL_PENDING',due=self.clock())
            elif p['status']=='SELL_PENDING':
                if not p.get('sale_held'):
                    sale=await self.sell(r,s['sell'],p['remaining'])
                    if not 0<=self.clock()-float(sale['ts'])<=3:raise ValueError('Устарела цена продажи')
                    self.charge(state,p,sale['gas'])
                    p['remaining']=str(D(p['remaining'])-D(sale['sold'])-D(sale['dust']))
                    p['dust']=str(D(p['dust'])+D(sale['dust']))
                    p['sold']=str(D(p['sold'])+D(sale['sold']))
                    p['proceeds']=str(D(p['proceeds'])+D(sale['cash']))
                    p['sell_fees']=str(D(p['sell_fees'])+D(sale['fee']))
                    p['fills'].append(dict(sale,stage='SELL',ts=self.clock()))
                    if D(p['remaining'])==0:p.update(status='RETURN_PENDING',due=self.clock())
                    else:p['due']=self.clock()+3
                else:p['due']=self.clock()+3
            elif p['status']=='RETURN_PENDING':
                if s['sell']==p['source']:
                    self.finish(state,p,p['proceeds'])
                else:
                    t=await self.transfer(r,'USDT',s['sell'],p['source'],p['proceeds'],networks)
                    self.charge(state,p,t['gas']);self.credit(state,p,s['sell'],t['dust'])
                    p.update(status='QUOTE_IN_TRANSIT',due=self.clock()+position_delay(p),return_credit=t['amount'],return_transfer=t)
            elif p['status']=='QUOTE_IN_TRANSIT':
                if p['source']=='bybit':p['return_credit']=str(self.received(networks['USDT'],p['return_transfer']['gross']))
                self.finish(state,p,p['return_credit'])
            else:raise ValueError('DEX_TRANSFER_STATE_UNKNOWN')
            state['cash']=self.paper.available(state);state['last_error']=''
            await self.paper.save(state,'DEX_TRANSFER_STAGE',p)
        except asyncio.CancelledError:raise
        except Exception as exc:
            # No writes/executions occurred before commit. Keep the prior stage,
            # including all already committed partial fills, on external errors.
            state=copy.deepcopy(self.paper.state);state['position']['due']=self.clock()+15
            state['last_error']=str(exc)[:200] if isinstance(exc,ValueError) else type(exc).__name__
            await self.paper.save(state,'DEX_TRANSFER_WAIT',dict(id=p['id'],reason=state['last_error']))

    async def cancel_unlocked(self,state,p):
        if p['status']!='BUY_PENDING':raise ValueError('Покупка уже отправлена по модели')
        self.credit(state,p,p['signal']['buy'],p['purchase_budget'])
        self.finish(state,p,0,'CANCELLED_BEFORE_BUY');state['cash']=self.paper.available(state)
        await self.paper.save(state,'DEX_TRANSFER_CANCEL',p)
        return p

    async def cycle(self):
        async with self.feed.lock:
            # Monitoring is independent from the new-entry switch and mode.
            await self.paper.monitor(self.feed)
            if self.feed.position_card:await self.paper.sync_cards(self.feed.position_card,self.feed.remove_position_card)
            await self.paper.flush(self.feed.notify,quiet_lifecycle=self.feed.position_card is not None)
            if not self.entry_enabled or not self.feed.entry_enabled or self.paused() or not await self.store.enabled() or self.paper.positions:return []
            catalog=self.catalog()
            if not catalog:return []
            r=catalog[(self.index//2)%len(catalog)];direction=self.index%2;self.index+=1
            a,b=('bybit','dex'+str(r['chain_id'])) if direction==0 else ('dex'+str(r['chain_id']),'bybit')
            self.last_economics=None
            try:
                from .dex_request_budget import background
                token=background.set(True)
                try:
                    e=await self.review(r['spot_symbol'],a,b,route=r)
                finally:
                    background.reset(token)
                self.checked+=1
                self.last_reason=e['reasons'][0] if e['reasons'] else 'READY'
                if not e['reasons']:
                    key=f'dex-transfer:{r["spot_symbol"]}:{a}:{b}:{int(self.clock()//300)}'
                    e['id']=hashlib.sha256(key.encode()).hexdigest()[:16]
                    await self.store.enqueue(key,'spot',e)
                    control=await self.store.control()
                    if control['mode']=='auto' and not self.paper.positions:
                        await self.paper.open(dict(signal=e,transfer=e['transfer']))
                    await self.feed.flush()
            except ValueError as exc:
                self.checked+=1;self.last_reason=str(exc)
            if self.last_reason!='READY':
                self.reasons[self.last_reason]=self.reasons.get(self.last_reason,0)+1
            if self.last_reason!='READY':
                key=f'dex-miss:{r["spot_symbol"]}:{a}:{b}:{int(self.clock()//60)}'
                await self.store.enqueue(key,'miss',dict(reason=self.last_reason,route=r,buy=a,sell=b,economics=self.last_economics))
                await self.store.delivered(key)
            from .scan_audit import save
            await save(self.store.path,'transfer',dict(ts=self.clock(),checked=self.checked,routes=len(catalog),reason=self.last_reason,reasons=self.reasons,economics=self.last_economics))
            if self.checked%20==1:
                logging.getLogger('arbitrage').info('DEX_TRANSFER_SCAN | checked=%s | routes=%s | reason=%s',self.checked,len(catalog),self.last_reason)
            return []
