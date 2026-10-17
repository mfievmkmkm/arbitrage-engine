"""Single-use PAPER operator actions. No exchange write API is imported.

An in-transit transfer cannot be cancelled or credited by a close button.
Pre-transfer spot changes can be unwound using fresh, possibly partial fills.
"""
import asyncio
import copy
import hashlib
import json
import secrets
from .strategy_math import D, ZERO, ONE, Invalid, plain, common_quantity


class Operator:
    def policy_fingerprint(self):
        return hashlib.sha256(json.dumps(plain(self.p),sort_keys=True).encode()).hexdigest()

    @staticmethod
    def fingerprint(pos):
        keys=('id','status','cash_result','residual','matched','transfers','manual_close_requested',
              'exit_a_remaining','exit_b_remaining','plan')
        data={k:pos.get(k) for k in keys}
        return hashlib.sha256(json.dumps(plain(data),sort_keys=True).encode()).hexdigest()

    def _issue_action(self,pos,action):
        tickets=getattr(self,'action_tickets',{})
        now=self.clock();tickets={k:v for k,v in tickets.items() if 0<=now-v['ts']<=30}
        if len(tickets)>=40:
            tickets.pop(next(iter(tickets)))
        token=secrets.token_hex(12)
        tickets[token]=dict(pid=str(pos['id']),action=action,ts=now,fingerprint=self.fingerprint(pos))
        self.action_tickets=tickets
        return token

    async def inspect_position(self,pid,source):
        async with self.lock:
            pos=copy.deepcopy(self.positions.get(str(pid)))
            if pos is None:
                raise Invalid('INVENTORY_POSITION_NOT_OPEN')
        p=pos['plan'];result=dict(position=pos,ts=self.clock(),tokens={})
        if pos['transfers']:
            result.update(kind='transfers',cycle_net=str(D(pos['cash_result'])-D(pos.get('base_fee_value',0))))
        else:
            a,b=await asyncio.gather(source.book(p['buy'],p['asset']),source.book(p['sell'],p['asset']))
            for row,venue in ((a,p['buy']),(b,p['sell'])):
                self.fresh_book(row)
                if row['venue']!=venue or row['asset']!=p['asset']:
                    raise Invalid('INVENTORY_EXECUTION_BOOK_IDENTITY_MISMATCH')
            from .inventory_paper import fill
            qa=D(pos.get('exit_a_remaining',D(pos['matched'])+D(pos['residual'])))
            qb=D(pos.get('exit_b_remaining',pos['matched']))
            fa,ca,fee_a=fill(a,qa,'sell') if qa>0 else (ZERO,ZERO,ZERO)
            budget=D(self.state['balances'].get(p['sell']+'|USDT',0))
            fb,cb,fee_b=fill(b,qb,'buy',cash_limit=budget) if qb>0 else (ZERO,ZERO,ZERO)
            result.update(kind='spot',sell_a_quantity=str(qa),buy_b_quantity=str(qb),
                close_net=str(D(pos['cash_result'])+ca-fee_a-cb-fee_b) if fa==qa and fb==qb else None,
                close_fees=str(fee_a+fee_b),book_a=a,book_b=b,
                available_a=str(fa),available_b=str(fb))
            rb=source.rebalance(p['asset'],p['buy'],p['sell'])
            can_transfer=(D(pos['residual'])==0 and D(pos['matched'])>0 and rb.get('verified') is True
                and ZERO<=D(self.clock())-D(rb['ts'])<=300)
            if can_transfer:
                q=D(pos['matched']);restore=D(pos['matched_buy_cash'])
                can_transfer=(q>=D(rb['base']['minimum']) and restore>=D(rb['quote']['minimum'])
                    and D(self.state['balances'].get(p['buy']+'|'+p['asset'],0))>=q+D(rb['base']['fee'])
                    and D(self.state['balances'].get(p['sell']+'|USDT',0))>=restore+D(rb['quote']['fee']))
            if can_transfer:
                result['rebalance_net']=str(D(pos['cash_result'])-D(rb['quote']['fee'])-D(rb['base']['fee'])*D(pos['buy_price']))
                result['rebalance']=rb
        async with self.lock:
            current=self.positions.get(str(pid))
            if current is None or self.fingerprint(current)!=self.fingerprint(pos):
                raise Invalid('INVENTORY_ACTION_STALE')
            if result['kind']=='transfers':
                if not current.get('manual_close_requested'):
                    result['tokens']['finish']=self._issue_action(current,'finish')
            elif current['status']!='MANUAL_EXIT_PENDING':
                result['tokens']['close']=self._issue_action(current,'close')
                if result.get('rebalance') and D(current['residual'])==0 and D(current['matched'])>0:
                    result['tokens']['transfer']=self._issue_action(current,'transfer')
            for action,token in result['tokens'].items():
                self.action_tickets[token]['preview']={k:copy.deepcopy(result[k]) for k in ('close_net','rebalance_net','cycle_net','ts','rebalance') if k in result}
                self.action_tickets[token]['asset']=p['asset']
        return result

    def operator_exit_step(self,state,pos,a,b):
        from .inventory_paper import fill
        p=pos['plan']
        for book,venue in ((a,p['buy']),(b,p['sell'])):
            self.fresh_book(book)
            if book['venue']!=venue or book['asset']!=p['asset']:
                raise Invalid('INVENTORY_EXECUTION_BOOK_IDENTITY_MISMATCH')
        for label,book,side,key in (('a',a,'sell','exit_a_remaining'),('b',b,'buy','exit_b_remaining')):
            requested=D(pos[key]);rate=D(book['fee_rate'])
            if side=='buy':
                cash=D(state['balances'].get(p['sell']+'|USDT',0))
                requested=min(requested,cash/(D(book['ask'])*(ONE+rate)))
            if requested<=0:
                continue
            requested=common_quantity(requested,[book['step']])
            if requested<D(book['min_qty']) or requested*D(book['bid' if side=='sell' else 'ask'])<D(book['min_notional']):
                pos['data_error']='MANUAL_EXIT_NATIVE_MINIMUM_OR_CASH_LOW';continue
            q,cash,fee=fill(book,requested,side,
                cash_limit=D(state['balances'].get(p['sell']+'|USDT',0)) if side=='buy' else None)
            if q<=0:
                pos['data_error']='MANUAL_EXIT_DEPTH_LOW';continue
            sign=ONE if side=='sell' else -ONE
            self.change(state,book['venue']+'|'+p['asset'],-q*sign)
            self.change(state,book['venue']+'|USDT',(cash-fee) if side=='sell' else -cash-fee)
            pos['cash_result']=str(D(pos['cash_result'])+cash*sign-fee)
            pos[key]=str(D(pos[key])-q)
            pos['fills'].append(dict(side='manual_exit_'+side,venue=book['venue'],quantity=str(q),
                                     cash=str(cash),fee=str(fee),ts=book['ts']))
            state['fees']=str(D(state['fees'])+fee)
        pos['residual']=str(abs(D(pos['exit_a_remaining'])-D(pos['exit_b_remaining'])))
        if D(pos['exit_a_remaining'])==0 and D(pos['exit_b_remaining'])==0:
            pos['matched']='0';pos.pop('data_error',None)
            self.finish(state,pos,'MANUAL_SPOT_UNWIND')
        else:
            pos['status']='MANUAL_EXIT_PENDING'

    async def peek_action(self,token):
        async with self.lock:
            item=getattr(self,'action_tickets',{}).get(token)
            if item is None or not 0<=self.clock()-item['ts']<=30:
                raise Invalid('INVENTORY_ACTION_EXPIRED')
            pos=self.positions.get(item['pid'])
            if pos is None or self.fingerprint(pos)!=item['fingerprint']:
                raise Invalid('INVENTORY_ACTION_STALE')
            return copy.deepcopy(item)

    async def request_action(self,token,source):
        async with self.lock:
            item=getattr(self,'action_tickets',{}).pop(token,None)
            if item is None or not 0<=self.clock()-item['ts']<=30:
                raise Invalid('INVENTORY_ACTION_EXPIRED')
            pos=self.positions.get(item['pid'])
            if pos is None or self.fingerprint(pos)!=item['fingerprint']:
                raise Invalid('INVENTORY_ACTION_STALE')
            p=copy.deepcopy(pos['plan'])
        a=b=rb=None
        if item['action']=='close':
            a,b=await asyncio.gather(source.book(p['buy'],p['asset']),source.book(p['sell'],p['asset']))
        elif item['action']=='transfer':
            await asyncio.gather(*(source.currencies(v) for v in (p['buy'],p['sell'])))
            rb=source.rebalance(p['asset'],p['buy'],p['sell'])
            if rb.get('verified') is not True or not ZERO<=D(self.clock())-D(rb['ts'])<=300:
                raise Invalid('INVENTORY_NETWORK_STATUS_UNKNOWN')
            old=item.get('preview',{}).get('rebalance')
            for name in ('base','quote'):
                if (rb[name].get('available') is not True or D(rb[name]['fee'])<0
                        or D(rb[name]['minimum'])<0 or D(rb[name]['delay_seconds'])<=0):
                    raise Invalid('INVENTORY_NETWORK_STATUS_UNKNOWN')
                if old is None or any(rb[name][k]!=old[name][k] for k in ('network','fee','minimum')):
                    raise Invalid('INVENTORY_ACTION_COST_CHANGED')
        async with self.lock:
            current=self.positions.get(item['pid'])
            if current is None or self.fingerprint(current)!=item['fingerprint']:
                raise Invalid('INVENTORY_ACTION_STALE')
            if not 0<=self.clock()-item['ts']<=30:
                raise Invalid('INVENTORY_ACTION_EXPIRED')
            state=copy.deepcopy(self.state);pos=state['positions'][item['pid']]
            pos['manual_close_requested']=True
            if item['action']=='finish':
                if not pos['transfers']:
                    raise Invalid('INVENTORY_ACTION_STALE')
                # Neither balances nor due dates change. Monitoring remains live.
                pos['manual_finish_requested_at']=str(self.clock())
            elif item['action']=='transfer':
                if pos['transfers'] or D(pos['residual'])!=0:
                    raise Invalid('INVENTORY_ACTION_STALE')
                pos['plan']['rebalance']=rb
                self.transfers(state,pos)
            elif item['action']=='close':
                if pos['transfers']:
                    raise Invalid('INVENTORY_TRANSFER_CANNOT_BE_CANCELLED')
                pos.update(status='MANUAL_EXIT_PENDING',exit_a_remaining=str(D(pos['residual'])+D(pos['matched'])),
                           exit_b_remaining=pos['matched'])
                self.operator_exit_step(state,pos,a,b)
            else:
                raise Invalid('INVENTORY_ACTION_UNKNOWN')
            if pos['status']=='CLOSED':
                self.enqueue(state,'CLOSE',pos)
            await self.save(state,'INVENTORY_MANUAL_'+item['action'].upper(),pos,[pos])
        await self.flush_notifications()
        return copy.deepcopy(pos)

    async def offer_candidate(self,plan):
        async with self.lock:
            now=D(self.clock());state=copy.deepcopy(self.state);key=plan['route']
            if now-D(state.get('signal_last',{}).get(key,-1e12))<300:
                return
            # A queued old candidate is replaced rather than growing an outage queue.
            for queue_key,item in list(state.get('notifications',{}).items()):
                if item['event']=='CANDIDATE' and item['position']['plan']['route']==key:
                    state['notifications'].pop(queue_key)
            state.setdefault('signal_last',{})[key]=str(now)
            signal=dict(id='signal-'+hashlib.sha256(key.encode()).hexdigest()[:12]+'-'+str(int(now)),
                        plan=copy.deepcopy(plan),signal_ts=str(now))
            self.enqueue(state,'CANDIDATE',signal)
            await self.save(state,'INVENTORY_CANDIDATE',signal)
        await self.flush_notifications()
