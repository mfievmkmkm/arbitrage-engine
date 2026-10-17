"""Durable sequential-transfer PAPER with later market exits, never old bids."""
import asyncio
import copy
import json
from .strategy_math import D, ONE, ZERO, common_quantity
from .inventory_paper import fill, day
from .transfer_signal_math import limits


class Paper:
    def __init__(self,store,clock):
        self.store,self.clock=store,clock
        self.lock=asyncio.Lock();self.state=None;self.version=0

    @property
    def positions(self):
        p=(self.state or {}).get('position')
        return {p['id']:p} if p else {}

    async def init(self):
        import aiosqlite
        initial=dict(cash='250',balances={},home='',realized='0',closed=0,wins=0,losses=0,position=None,daily={},last_error='',notifications={})
        async with aiosqlite.connect(self.store.path) as db:
            await db.executescript('''CREATE TABLE IF NOT EXISTS signal_transfer_state(id INTEGER PRIMARY KEY,version INTEGER,payload TEXT);
                CREATE TABLE IF NOT EXISTS signal_transfer_events(id INTEGER PRIMARY KEY,ts REAL,reason TEXT,payload TEXT);''')
            await db.execute('INSERT OR IGNORE INTO signal_transfer_state VALUES(1,0,?)',(json.dumps(initial),))
            self.version,payload=await (await db.execute('SELECT version,payload FROM signal_transfer_state WHERE id=1')).fetchone()
            self.state=json.loads(payload);await db.commit()
        if 'balances' not in self.state:
            # This draft never shipped, but refuse to turn an unknown balance
            # location into free funds on every venue.
            self.state['balances']={};self.state['home']=''

    def funding_source(self,buy,budget):
        if not self.state or not self.state.get('home'):
            return None
        balances=self.state['balances']
        if D(balances.get(buy,'0'))>=D(budget):return buy
        return next((v for v,cash in balances.items() if D(cash)>=D(budget)),None)

    async def set_home(self,venue,allowed):
        if venue not in allowed:raise ValueError('На бирже нет настроенного spot API')
        async with self.lock:
            if self.state.get('home') or self.state['closed'] or self.state['position']:
                raise ValueError('Исходную биржу можно выбрать только до первого PAPER-цикла')
            state=copy.deepcopy(self.state);state['home']=venue;state['balances']={venue:'250'}
            await self.save(state,'PAPER_STARTING_VENUE',dict(venue=venue,capital='250'))

    @staticmethod
    def available(state):
        return str(sum((D(v) for v in state['balances'].values()),ZERO))

    async def save(self,state,reason,payload):
        import aiosqlite
        async with aiosqlite.connect(self.store.path) as db:
            await db.execute('BEGIN IMMEDIATE')
            c=await db.execute('UPDATE signal_transfer_state SET version=version+1,payload=? WHERE id=1 AND version=?',
                               (json.dumps(state,allow_nan=False),self.version))
            if c.rowcount!=1:raise ValueError('Другой процесс изменил PAPER-счёт; перезапустите экземпляр')
            await db.execute('INSERT INTO signal_transfer_events(ts,reason,payload) VALUES(?,?,?)',
                             (self.clock(),reason,json.dumps(payload,allow_nan=False)))
            await db.commit()
        self.state=state;self.version+=1

    async def open(self,review):
        async with self.lock:
            e=review['signal'];plan=review['transfer']
            if plan is None or plan['reasons']:
                raise ValueError('Переводная модель не прошла фильтры')
            if not 0<=self.clock()-e['ts']<=3:
                raise ValueError('Нужны новые котировки')
            if self.state['position']:
                raise ValueError('Сначала завершите текущую PAPER-позицию')
            if D(self.state['realized'])<=D('-2.5') or D(self.state['daily'].get(day(self.clock()),'0'))<=D('-1.25'):
                raise ValueError('Достигнут лимит убытка PAPER: день $1.25, весь тест $2.50')
            if not self.state.get('home'):raise ValueError('Сначала выбери исходную биржу PAPER-капитала')
            state=copy.deepcopy(self.state)
            p=dict(id=str(self.version+1),signal=dict(symbol=e['symbol'],buy=e['buy'],sell=e['sell']),
                   plan=plan,opened=self.clock(),due=self.clock()+900,status='BASE_IN_TRANSIT',
                   remaining=plan['received_qty'],proceeds='0',sell_fees='0',dust='0',sold='0',fills=[],funding_fee='0')
            if plan.get('funding_route'):
                src=plan['funding_source'];amount=D(plan['funding_amount'])
                if D(state['balances'].get(src,'0'))<amount:raise ValueError('Недостаточно USDT для предварительного перевода')
                state['balances'][src]=str(D(state['balances'][src])-amount)
                p.update(status='USDT_TO_BUY_IN_TRANSIT',funding_fee=plan['funding_route']['fee'])
            else:
                if D(state['balances'].get(e['buy'],'0'))<D(plan['spent']):raise ValueError('На бирже покупки недостаточно виртуального USDT')
                state['balances'][e['buy']]=str(D(state['balances'][e['buy']])-D(plan['spent']))
            state['cash']=self.available(state)
            state['position']=p;state['last_error']=''
            message=(f"🧪 PAPER перевод #{p['id']}: {e['symbol']} {e['buy']} → {e['sell']}. "
                     +(f"Сначала USDT {plan['funding_source']} → {e['buy']}; покупка после нового стакана." if p['status']=='USDT_TO_BUY_IN_TRANSIT' else f"Куплено по модели на ${float(plan['spent']):.4f}.")+
                     ' Продажа по будущему стакану после зачисления монеты; найденный спред не зафиксирован.')
            key='open:'+p['id'];state['notifications'][key]=message
            await self.save(state,'FUNDING_RESERVATION' if p['status']=='USDT_TO_BUY_IN_TRANSIT' else 'BUY_AND_BASE_RESERVATION',p)
            return p

    async def monitor(self,source):
        async with self.lock:
            p0=self.state.get('position')
            if not p0 or self.clock()<p0['due']:return
            state=copy.deepcopy(self.state);p=state['position'];s=p['signal'];plan=p['plan']
            try:
                if p['status']=='USDT_TO_BUY_IN_TRANSIT':
                    route=await source.quote_route(plan['funding_source'],s['buy'])
                    if route['network']!=plan['funding_route']['network']:
                        raise ValueError('Сеть предварительного перевода поменялась')
                    state['balances'][s['buy']]=str(D(state['balances'].get(s['buy'],'0'))+D(plan['funding_credit']))
                    state['cash']=self.available(state)
                    p.update(status='BUY_PENDING',due=self.clock()+2)
                    await self.save(state,'FUNDING_CREDIT',p);return
                if p['status']=='BUY_PENDING':
                    if not source.entry_enabled:
                        p['due']=self.clock()+15
                        await self.save(state,'BUY_PAUSED',p);return
                    try:
                        fresh=await source.evaluate(s['symbol'],s['buy'],s['sell'])
                        if not fresh or not fresh.get('transfer') or fresh['reasons']:
                            raise ValueError('На новых котировках экономика покупки не прошла фильтры')
                        new=fresh['transfer'];cost=D(new['spent'])
                        control=await source.store.control()
                        if D(new['stress_net'])-D(p['funding_fee'])<D(control['min_net']):
                            raise ValueError('После комиссии первого перевода стресс-NET слишком мал')
                        if cost>D(state['balances'][s['buy']]):raise ValueError('После перевода не хватает USDT на новую котировку')
                    except ValueError as exc:
                        # USDT is already at the buy venue. Do not invent a
                        # refund or spend it on an unprofitable coin.
                        loss=-D(p['funding_fee'])
                        state['realized']=str(D(state['realized'])+loss)
                        state['closed']+=1;state['losses']+=1
                        today=day(self.clock());state['daily'][today]=str(D(state['daily'].get(today,'0'))+loss)
                        p.update(status='ABORTED_AFTER_FUNDING',closed=self.clock(),net=str(loss),reason=str(exc))
                        state['position']=None
                        state['notifications']['abort:'+p['id']]=f"⛔ PAPER #{p['id']}: покупка отменена после зачисления USDT: {exc}. Потеря комиссии ${float(-loss):.4f}; USDT остаётся на {s['buy']}."
                        await self.save(state,'BUY_ABORTED',p);return
                    state['balances'][s['buy']]=str(D(state['balances'][s['buy']])-cost)
                    state['cash']=self.available(state)
                    p.update(plan=new,remaining=new['received_qty'],status='BASE_IN_TRANSIT',due=self.clock()+900)
                    state['notifications']['buy:'+p['id']]=f"🧪 PAPER #{p['id']}: после получения USDT обновили стаканы, покупка смоделирована на ${float(cost):.4f}. Теперь ждём BASE на продаже."
                    await self.save(state,'BUY_AFTER_FUNDING',p);return
                # Conservative receipt model: route must still be available.
                base,quote=await source.route(s['symbol'].split('/')[0],s['buy'],s['sell'])
                if base['network']!=plan['base_route']['network'] or quote['network']!=plan['quote_route']['network']:
                    raise ValueError('Изменилась сеть; зачисление не моделируется на другой сети')
                if p['status']=='QUOTE_IN_TRANSIT':
                    net=D(p['return_credit'])-D(plan['spent'])-D(p['funding_fee'])
                    state['balances'][s['buy']]=str(D(state['balances'].get(s['buy'],'0'))+D(p['return_credit']))
                    state['cash']=self.available(state)
                    state['realized']=str(D(state['realized'])+net);state['closed']+=1
                    state['wins' if net>0 else 'losses']+=1
                    today=day(self.clock());state['daily'][today]=str(D(state['daily'].get(today,'0'))+net)
                    p.update(status='CLOSED',closed=self.clock(),net=str(net))
                    state['position']=None;state['last_error']=''
                    state['notifications']['close:'+p['id']]=f"🧪 PAPER перевод #{p['id']} завершён. Фактический NET модели {float(net):+.4f} USDT, после более поздней продажи и обоих переводов. Резерв $0.10 не списывался как реальная комиссия."
                    await self.save(state,'QUOTE_CREDIT_AND_CLOSE',p);return
                p['status']='SELL_PENDING'
                b=await source.book(s['sell'],s['symbol'])
                if not ZERO<=D(self.clock())-D(b['ts'])<=3:raise ValueError('Нет свежего стакана выхода')
                qty=common_quantity(D(p['remaining']),[b['step']])
                if qty>0:
                    sold,cash,fee=fill(b,qty,'sell')
                    if sold>0:
                        limits(source.clients[s['sell']].markets[s['symbol']],sold,cash)
                        p['remaining']=str(D(p['remaining'])-sold);p['sold']=str(D(p['sold'])+sold)
                        p['proceeds']=str(D(p['proceeds'])+cash-fee);p['sell_fees']=str(D(p['sell_fees'])+fee)
                        p['fills'].append(dict(ts=self.clock(),qty=str(sold),cash=str(cash),fee=str(fee)))
                if D(p['remaining'])<D(b['step']):
                    # Unsellable dust is explicitly written off in PAPER only.
                    p['dust']=p['remaining'];p['remaining']='0'
                if D(p['remaining'])==0:
                    if quote.get('deposit_fee') is None or quote.get('deposit_fee_percentage') is not False or D(quote['deposit_fee'])!=0:
                        raise ValueError('Не подтверждена комиссия обратного депозита USDT')
                    amount=D(p['proceeds']);fee=D(quote['fee']);credit=amount-fee
                    if amount<D(quote['withdraw_minimum']) or credit<D(quote['deposit_minimum']):
                        raise ValueError('Выручка ниже минимума обратного перевода: средства остаются на продаже')
                    p.update(return_credit=str(credit),return_fee=str(fee),status='QUOTE_IN_TRANSIT',due=self.clock()+900)
                else:p['due']=self.clock()+2
                state['last_error']=''
                await self.save(state,'SALE_OR_QUOTE_RESERVATION',p)
            except asyncio.CancelledError:raise
            except Exception as exc:
                # A completed partial sale stays committed, even if the next
                # transfer check fails. No second sale of the same BASE.
                state['last_error']=str(exc)[:300] if isinstance(exc,ValueError) else type(exc).__name__
                p['due']=self.clock()+15
                if self.clock()-p['opened']>=3600:
                    state['notifications'].setdefault('delay:'+p['id'],f"⚠ PAPER перевод #{p['id']}: более часа без завершения. Новые входы заблокированы, сопровождение продолжается. "+state['last_error'])
                await self.save(state,'MONITOR_WAIT',dict(position=p,reason=state['last_error']))

    async def flush(self,notify):
        # Persistent outbox with at-least-once delivery. State lock prevents
        # UI opens racing a completion notification acknowledgement.
        async with self.lock:
            for key,message in list(self.state['notifications'].items()):
                await notify(message)
                state=copy.deepcopy(self.state);state['notifications'].pop(key,None)
                await self.save(state,'NOTIFIED',dict(key=key))

    def render(self):
        from html import escape
        s=self.state;p=s['position']
        text=(f"🧪 <b>PAPER · USDT → покупка → BASE → продажа</b>\nОтдельный виртуальный счёт, старт $250.\nСвободно ${float(s['cash']):.4f} · завершено {s['closed']} · NET {float(s['realized']):+.4f}\n"
              f"Плюс {s['wins']} / минус {s['losses']}. Не суммировать с прежними счетами.\n")
        text+='Стартовая биржа: '+escape(s.get('home') or 'не выбрана')+'; USDT: '+', '.join(escape(k)+' $'+str(float(v)) for k,v in s.get('balances',{}).items())+'\n'
        if p:
            text+=f"#{p['id']} · {escape(p['signal']['symbol'])} · {p['status']}\nBASE осталось {escape(p['remaining'])}, продано {escape(p['sold'])}; выручка ${float(p['proceeds']):.4f}\n"
        text+='Последняя проблема: '+escape(s['last_error'] or 'нет')
        return text+'\nЦена будущей продажи берётся после задержки. Задержка 900 сек на каждый перевод — допущение, не измерение. Частичные продажи и убытки сохраняются; минусовой выход не запрещён. Непродаваемая пыль списывается в модели в ноль. Реальных транзакций нет.'
