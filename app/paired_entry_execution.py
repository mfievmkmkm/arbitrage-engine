"""Read-only, staged PAPER entry. Quotes are modeled fills, never venue fills.

DEX swaps are atomic in this model. The later CEX IOC can fill partially from
fresh visible depth. Every phase is committed by the owner engine before the
next phase; an unhedged remainder is retried briefly, then sold back on DEX.
"""
import asyncio
import copy
from decimal import Decimal
from .native_order_plan import market, format_value
from .public_books import normalize


class Executor:
    def __init__(self,source,clock,max_unhedged_seconds=10):
        self.source,self.clock=source,clock
        self.max_unhedged_seconds=max_unhedged_seconds
        self.allow_start=lambda:True

    def reserve(self,p):
        p=copy.deepcopy(p)
        p.update(status='ENTRY_RESERVED',execution_phase='DEX_BUY',entry_reserved_at=self.clock(),
                 entry_mode=self.source.operator_profile().get('mode'),execution_events=[],
                 dex_filled_qty=0,hedged_base_qty=0,hedged_contracts=0,cex_entry_cash=0,
                 execution_model='STAGED_VISIBLE_DEPTH_PAPER_V1',net=0)
        return p

    async def advance(self,p):
        phase=p['execution_phase']
        try:
            if phase=='DEX_BUY':
                current=self.source.operator_profile()
                if p.get('operator_exit_requested') is not None or not self.allow_start() or current.get('mode')=='observe' or current.get('mode')!=p.get('entry_mode'):
                    return self.cancel(p,'ENTRY_INTENT_CHANGED')
                route=dict(p['paired_route'],_execution_trace=p.get('trace_id'))
                result=await self.source.entry(route)
                if not result.get('ok'):
                    if self.clock()-p['entry_reserved_at']>=30:return self.cancel(p,result.get('reason','ENTRY_RECHECK_FAILED'))
                    p['execution_error']=result.get('reason','ENTRY_RECHECK_FAILED')
                    return p,'WAIT',None
                fresh=result['position']
                # New metadata may not increase the already reserved exposure.
                if fresh['reserve']>p['reserve']+1e-8:return self.cancel(p,'ENTRY_RESERVATION_EXCEEDED')
                kept={k:p[k] for k in ('id','trace_id','entry_reserved_at','entry_mode','execution_events','execution_model','reserve') if k in p}
                p.update(fresh);p.update(kept)
                p.update(status='HEDGE_PENDING',execution_phase='CEX_HEDGE',dex_filled_qty=p['virtual_asset_qty'],
                         target_contracts=p['cex_contracts'],target_base_qty=p['base_qty'],hedged_contracts=0,
                         hedged_base_qty=0,cex_entry_cash=0,first_fill_at=self.clock(),cex_first_fill_at=None,
                         entry_fees=p['entry_gas'],net=0)
                p['execution_events'].append(dict(stage='DEX_BUY',ts=self.clock(),base_qty=p['virtual_asset_qty'],
                    cash=p['entry_cash'],gas=p['entry_gas'],quote_at=p['entry_dex']['ts'],mode='MODELED_QUOTE_FILL'))
                return p,'DEX_BUY_MODELED',None
            if phase=='CEX_HEDGE':
                if p.get('operator_exit_requested') is not None or self.clock()-p['first_fill_at']>=self.max_unhedged_seconds:
                    p.update(status='UNHEDGED_RECOVERY',execution_phase='DEX_UNWIND')
                    return p,'HEDGE_TIMEOUT',None
                source=self.source;route=source.route(p);c=source.public[p['cex_venue']]
                m=market(c,p['cex_symbol']);size=float(m['contractSize'])
                if size!=p['contract_size']:raise ValueError('DEX_VIRTUAL_CONTRACT_CHANGED')
                fee,_=await source.fee(route)  # Fee IO must precede the fresh book.
                started=self.clock()
                fetch=getattr(c,'fetch_priority_order_book',c.fetch_order_book)
                raw=await asyncio.wait_for(fetch(p['cex_symbol'],limit=20),3)
                b=normalize(raw,p['cex_symbol'],started,self.clock(),1.5)
                remaining=Decimal(str(p['target_contracts']))-Decimal(str(p['hedged_contracts']))
                slip=Decimal(str(route.get('slippage_bps',20)))/10000
                floor=max(Decimal(str(b['bids'][0][0]))*(1-slip),Decimal(str(p['entry_price']))*(1-slip))
                available=sum((Decimal(str(q)) for price,q in b['bids'] if Decimal(str(price))>=floor),Decimal(0))
                contracts=Decimal(str(format_value(c,p['cex_symbol'],min(remaining,available),'amount')))
                min_qty=Decimal(str((m.get('limits',{}).get('amount') or {}).get('min',0)))
                if contracts<=0 or contracts<min_qty:raise ValueError('PAPER_HEDGE_NO_EXECUTABLE_NATIVE_SIZE')
                left=contracts;cash=Decimal(0)
                for price,q in b['bids']:
                    if Decimal(str(price))<floor:break
                    take=min(left,Decimal(str(q)));cash+=take*Decimal(str(price))*Decimal(str(size));left-=take
                    if left==0:break
                minimum=(m.get('limits',{}).get('cost') or {}).get('min')
                if minimum is None or cash<Decimal(str(minimum)):raise ValueError('PAPER_HEDGE_BELOW_MINIMUM_COST')
                p['hedged_contracts']=float(Decimal(str(p['hedged_contracts']))+contracts)
                p['hedged_base_qty']=p['hedged_contracts']*size
                p['cex_entry_cash']+=float(cash);p['entry_fees']+=float(cash)*fee
                if p.get('cex_first_fill_at') is None:p['cex_first_fill_at']=self.clock()
                p['execution_error']=''
                p['execution_events'].append(dict(stage='CEX_SHORT',ts=self.clock(),contracts=float(contracts),
                    base_qty=float(contracts)*size,cash=float(cash),fee=float(cash)*fee,book=b,mode='MODELED_VISIBLE_IOC'))
                if Decimal(str(p['hedged_contracts']))>=Decimal(str(p['target_contracts'])):
                    self.hedged(p)
                    return p,'PAIR_HEDGED',None
                return p,'PARTIAL_HEDGE',None
            if phase=='DEX_UNWIND':
                source=self.source;route=source.route(p)
                if source.price_providers:
                    name=p.get('paired_quote_provider','0x')
                    provider=source.price_providers.get(name)
                    if provider is None:raise ValueError('PAIRED_POSITION_PROVIDER_UNAVAILABLE')
                    source=copy.copy(source);source.provider=provider
                scale=Decimal(10)**p['asset_decimals']
                kept=Decimal(str(p['hedged_contracts']))*Decimal(str(p['contract_size']))*scale
                if kept!=kept.to_integral_value():raise ValueError('DEX_PAIRED_BASE_QUANTUM_MISMATCH')
                sell_raw=int(p['asset_amount_raw'])-int(kept)
                if sell_raw<=0:
                    self.hedged(p);return p,'PAIR_HEDGED',None
                q=await source.price(route,True,sell_raw)
                gas,_,_=await source.gas(route,q)
                cash=float(Decimal(q['virtual_min_buy_raw'])/(Decimal(10)**p['stable_decimals']))
                # Carry the recovered cash against the original total spend.
                # This basis preserves the entire one-leg gain/loss in final PnL.
                p['entry_cash']-=cash;p['entry_fees']+=gas
                p['asset_amount_raw']=str(int(kept));p['virtual_asset_qty']=p['hedged_base_qty']
                p['dex_filled_qty']=p['hedged_base_qty']
                p['execution_events'].append(dict(stage='DEX_UNHEDGED_UNWIND',ts=self.clock(),sell_raw=str(sell_raw),
                    cash=cash,gas=gas,quote=q,mode='MODELED_QUOTE_FILL'))
                if p['hedged_base_qty']<=0:
                    p.update(status='CLOSED',closed_at=self.clock(),net=-p['entry_cash']-p['entry_fees'],
                             exit_reason='UNHEDGED_UNWIND',execution_phase='DONE')
                    return p,'UNHEDGED_UNWIND',p
                self.hedged(p)
                p['operator_exit_requested']=self.clock();p['execution_exit_reason']='PARTIAL_HEDGE_RECOVERY_EXIT'
                return p,'PARTIAL_HEDGE_RECOVERED',None
            raise ValueError('PAPER_ENTRY_PHASE_UNKNOWN')
        except asyncio.CancelledError:raise
        except Exception as exc:
            p['execution_error']=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
            p.setdefault('execution_events',[]).append(dict(stage=phase,ts=self.clock(),reason=p['execution_error'],mode='WAIT_NO_FILL'))
            p['execution_events']=p['execution_events'][-100:]
            return p,'WAIT',None

    def hedged(self,p):
        p.update(status='OPEN',execution_phase='DONE',base_qty=p['hedged_base_qty'],cex_contracts=p['hedged_contracts'],
                 entry_price=p['cex_entry_cash']/p['hedged_base_qty'],opened_at=p['cex_first_fill_at'],execution_error='',net=0)

    def cancel(self,p,reason):
        p.update(status='CANCELLED_BEFORE_FILL',execution_phase='DONE',execution_error=reason,closed_at=self.clock(),net=0)
        return p,'CANCELLED_BEFORE_FILL',None
