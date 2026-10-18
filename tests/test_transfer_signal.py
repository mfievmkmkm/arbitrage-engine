import asyncio
from types import SimpleNamespace as NS
import pytest
from app.strategy_math import D
from app.transfer_signal_math import plan
from app.transfer_signal_paper import Paper
from app.market_signal_store import Store
from app.signal_account_check import Accounts
from app.market_signals import Scanner


def books(price,side='buy',size='1',stamp=1000):
    return dict(ts=str(stamp),step='.001',fee_rate='.002',
                asks=[[str(price),size]],bids=[[str(D(price)-D('.1')),size]])


def routes():
    return (dict(network='SOL',fee='.001',withdraw_minimum='.001',deposit_minimum='.001',
                 deposit_fee='0',deposit_fee_percentage=False),
            dict(network='TRC20',fee='.05',withdraw_minimum='1',deposit_minimum='1',
                 deposit_fee='0',deposit_fee_percentage=False))


def market():
    return dict(limits=dict(amount=dict(min='.001'),cost=dict(min='1')))


def test_sequential_math_no_fixed_spread_and_stress_gate():
    p=plan(books('100'),books('107'),*routes())
    assert not p['reasons'] and D(p['buy_qty'])>D(p['sell_qty'])
    assert D(p['net_now'])>D(p['net_after_reserve'])
    assert D(p['scenarios'][0]['net'])>D(p['scenarios'][2]['net'])>D(p['scenarios'][-1]['net'])
    assert D(p['stress_net'])==D(p['scenarios'][2]['net'])
    assert D(p['break_even_sell'])>D(p['buy_vwap'])
    assert D(plan(books('100'),books('107'),*routes(),stress_drop=5)['stress_net'])<D(p['stress_net'])
    no_deposit=routes()[0]|dict(deposit_fee=None)
    assert 'комиссия депозита' in ' '.join(plan(books('100'),books('107'),no_deposit,routes()[1])['reasons'])
    expensive=routes()[0]|dict(fee='.25')
    with pytest.raises(ValueError,match='BASE'):
        plan(books('100'),books('107'),expensive,routes()[1])


def test_scanner_does_not_require_prefunded_base_on_sell_venue(tmp_path):
    async def go():
        scanner=Scanner(tmp_path/'db',None,clock=lambda:1000,venues=[],trade_venues={'buy','sell'})
        await scanner.init();await scanner.paper.set_home('buy',scanner.trade_venues)
        scanner.clients={'buy':NS(markets={'SOL/USDT':market()},urls={}),
                         'sell':NS(markets={'SOL/USDT':market()},urls={})}
        async def route(*args):return routes()
        async def book(venue,*args):return books('100' if venue=='buy' else '107',stamp=1000) | dict(ts=D(1000))
        scanner.route=route;scanner.book=book
        result=await scanner.evaluate('SOL/USDT','buy','sell')
        assert result['transfer'] and not result['reasons']
        assert D(result['transfer']['spent'])<=25
        assert 'Цена может измениться' in scanner.format(result)
    asyncio.run(go())


def test_paper_later_price_can_erase_initial_edge_and_restart(tmp_path):
    async def go():
        clock=[1000.0]
        store=Store(tmp_path/'db',clock=lambda:clock[0]);await store.init()
        paper=Paper(store,lambda:clock[0]);await paper.init()
        await paper.set_home('buy',{'buy','sell'})
        p=plan(books('100'),books('107'),*routes())
        e=dict(symbol='SOL/USDT',buy='buy',sell='sell',ts=clock[0])
        opened=await paper.open(dict(signal=e,transfer=p))
        assert opened['status']=='BASE_IN_TRANSIT'
        assert D(paper.state['cash'])==D('250')-D(p['spent'])
        with pytest.raises(ValueError,match='Сначала завершите'):
            await paper.open(dict(signal=e,transfer=p))
        restored=Paper(store,lambda:clock[0]);await restored.init()
        assert restored.positions and restored.state['cash']==paper.state['cash']
        class Source:
            clients={'sell':NS(markets={'SOL/USDT':market()})}
            async def route(self,*args):return routes()
            async def book(self,*args):return books('100','sell',stamp=clock[0]) | dict(bids=[['99','1']])
        source=Source()
        clock[0]=1901
        await restored.monitor(source)
        assert restored.state['position']['status']=='QUOTE_IN_TRANSIT'
        assert D(restored.state['position']['proceeds'])<D(p['sell_cash'])
        clock[0]=2802
        await restored.monitor(source)
        assert restored.state['closed']==1 and restored.state['losses']==1
        assert D(restored.state['realized'])<0
        assert D(restored.state['cash'])==D('250')+D(restored.state['realized'])
        sent=[]
        async def notify(msg):sent.append(msg)
        await restored.flush(notify)
        assert len(sent)==2 and 'NET модели' in sent[-1]
        await restored.flush(notify)
        assert len(sent)==2
    asyncio.run(go())


def test_partial_sale_persists_and_waits_for_recovery(tmp_path):
    async def go():
        clock=[1000.0];store=Store(tmp_path/'db',lambda:clock[0]);await store.init()
        paper=Paper(store,lambda:clock[0]);await paper.init()
        await paper.set_home('buy',{'buy','sell'})
        p=plan(books('100'),books('107'),*routes())
        await paper.open(dict(signal=dict(symbol='SOL/USDT',buy='buy',sell='sell',ts=clock[0]),transfer=p))
        class Source:
            clients={'sell':NS(markets={'SOL/USDT':market()})}
            async def route(self,*args):return routes()
            async def book(self,*args):return books('107','sell',size='.1',stamp=clock[0])
        source=Source();clock[0]=1901;await paper.monitor(source)
        pos=paper.state['position'];assert pos['status']=='SELL_PENDING' and D(pos['remaining'])>0
        first=D(pos['sold']);cash=pos['proceeds']
        recovered=Paper(store,lambda:clock[0]);await recovered.init()
        source.book=lambda *args: immediate(books('107','sell',size='1',stamp=clock[0]))
        clock[0]=1904;await recovered.monitor(source)
        assert D(recovered.state['position']['sold'])>first
        assert D(recovered.state['position']['proceeds'])>D(cash)
        assert recovered.state['position']['status']=='QUOTE_IN_TRANSIT'
    async def immediate(x):return x
    asyncio.run(go())


def test_policy_and_read_only_account_reject_unknown(tmp_path):
    async def go():
        store=Store(tmp_path/'db');await store.init()
        assert (await store.control())['mode']=='manual'
        await store.set_control('mode','auto')
        assert (await Store(tmp_path/'db').control())['mode']=='auto'
        with pytest.raises(ValueError):await store.set_control('min_spread',1)
        with pytest.raises(ValueError):await store.set_control('budget',250)
        class Client:
            has={'fetchBalance':True,'fetchTradingFee':True}
            async def fetch_balance(self,params):return {'USDT':{'free':30}}
            async def fetch_trading_fee(self,symbol):return {'symbol':symbol,'taker':'.001'}
            async def create_order(self,*a):raise AssertionError('ORDER')
        data=await Accounts({'bybit':Client()},lambda:100).check('SOL/USDT','bybit','gate')
        assert data['accounts']['bybit']['free']['USDT']=='30'
        assert data['accounts']['bybit']['free']['SOL'] is None
        assert data['execution_authorized'] is False and 'нет полного API' not in str(data)
        assert data['reasons'] and 'gate' in data['reasons'][0]
    asyncio.run(go())


def test_usdt_relocation_rechecks_after_arrival_and_accounts_for_fee(tmp_path):
    async def go(abort=False):
        clock=[1000.0];store=Store(tmp_path/('abort.db' if abort else 'success.db'),lambda:clock[0]);await store.init()
        paper=Paper(store,lambda:clock[0]);await paper.init();await paper.set_home('home',{'home','buy','sell'})
        p=plan(books('100'),books('107'),*routes())
        p.update(funding_source='home',funding_route=routes()[1],funding_amount='25.05',funding_credit='25')
        signal=dict(symbol='SOL/USDT',buy='buy',sell='sell',ts=clock[0])
        assert paper.funding_source('buy',25)=='home'
        await paper.open(dict(signal=signal,transfer=p))
        assert paper.state['position']['status']=='USDT_TO_BUY_IN_TRANSIT'
        assert D(paper.state['balances']['home'])==D('224.95')
        class Source:
            entry_enabled=True
            clients={'sell':NS(markets={'SOL/USDT':market()})}
            async def quote_route(self,*args):return routes()[1]
            async def route(self,*args):return routes()
            async def evaluate(self,*args):
                if abort:return None
                return dict(transfer=plan(books('100'),books('107'),*routes()),reasons=[],trade_configured=True)
            async def book(self,*args):return books('107','sell',stamp=clock[0])
        source=Source();source.store=store;clock[0]=1901;await paper.monitor(source)
        assert paper.state['position']['status']=='BUY_PENDING'
        assert D(paper.state['balances']['buy'])==25
        clock[0]=1904;await paper.monitor(source)
        if abort:
            assert paper.state['position'] is None
            assert D(paper.state['realized'])==D('-.05')
            assert D(paper.state['cash'])==D('249.95')
            assert paper.funding_source('buy',25)=='buy'
        else:
            assert paper.state['position']['status']=='BASE_IN_TRANSIT'
            assert D(paper.state['balances']['buy'])<25
            clock[0]=2805;await paper.monitor(source)
            assert paper.state['position']['status']=='QUOTE_IN_TRANSIT'
            clock[0]=3706;await paper.monitor(source)
            assert paper.state['closed']==1
            assert D(paper.state['cash'])==D('250')+D(paper.state['realized'])
            assert D(paper.state['realized'])==D(p['net_now'])-D('.05')
    asyncio.run(go())
    asyncio.run(go(True))


def test_dedicated_position_card_and_operator_sale_hold(tmp_path):
    async def go():
        clock=[1000.0];store=Store(tmp_path/'db',lambda:clock[0]);await store.init()
        paper=Paper(store,lambda:clock[0]);await paper.init();await paper.set_home('buy',{'buy','sell'})
        p=plan(books('100'),books('107'),*routes())
        position=await paper.open(dict(signal=dict(symbol='SOL/USDT',buy='buy',sell='sell',ts=1000),transfer=p))
        calls=[]
        async def deliver(pos,text,message_id):
            calls.append((pos['status'],message_id,text));return 41
        await paper.sync_cards(deliver);await paper.sync_cards(deliver)
        assert len(calls)==1 and calls[0][1] is None and 'Цена продажи не закреплена' in calls[0][2]
        class Source:
            clients={'sell':NS(markets={'SOL/USDT':market()})}
            async def route(self,*args):return routes()
            async def book(self,*args):return books('107',stamp=clock[0]) | dict(ts=D(clock[0]))
        source=Source();clock[0]=1901
        source.book=lambda *args: immediate(books('107',size='.1',stamp=clock[0]) | dict(ts=D(clock[0])))
        await paper.monitor(source)
        assert paper.state['position']['status']=='SELL_PENDING'
        await paper.control(position['id'],'hold_sale')
        clock[0]=1904;await paper.monitor(source)
        assert paper.state['position']['sold']==paper.state['position']['fills'][0]['qty']
        restored=Paper(store,lambda:clock[0]);await restored.init()
        assert restored.state['position']['sale_held'] is True
        await restored.control(position['id'],'resume_sale')
        source.book=lambda *args: immediate(books('107',stamp=clock[0]) | dict(ts=D(clock[0])))
        await restored.monitor(source);await restored.sync_cards(deliver)
        assert calls[-1][1]==41 and restored.state['position']['status']=='QUOTE_IN_TRANSIT'
    async def immediate(x):return x
    asyncio.run(go())


def test_operator_can_cancel_only_before_buy_after_funding(tmp_path):
    async def go():
        clock=[1000.0];store=Store(tmp_path/'db',lambda:clock[0]);await store.init()
        paper=Paper(store,lambda:clock[0]);await paper.init();await paper.set_home('home',{'home','buy','sell'})
        p=plan(books('100'),books('107'),*routes())
        p.update(funding_source='home',funding_route=routes()[1],funding_amount='25.05',funding_credit='25')
        opened=await paper.open(dict(signal=dict(symbol='SOL/USDT',buy='buy',sell='sell',ts=1000),transfer=p))
        with pytest.raises(ValueError,match='недоступно'):
            await paper.control(opened['id'],'cancel_buy')
        class Source:
            async def quote_route(self,*args):return routes()[1]
        clock[0]=1901;await paper.monitor(Source())
        assert paper.state['position']['status']=='BUY_PENDING'
        await paper.control(opened['id'],'cancel_buy')
        assert paper.state['position'] is None
        assert D(paper.state['realized'])==D('-.05')
        assert D(paper.state['balances']['buy'])==25
        assert paper.state['last_position']['status']=='CANCELLED_BEFORE_BUY'
    asyncio.run(go())


def test_requested_transfer_size_and_spread_bounds_survive_restart(tmp_path):
    async def go():
        from app.market_signal_store import Store
        store=Store(tmp_path/'bounds.db');await store.init()
        await store.set_control('budget','100')
        await store.set_control('min_spread','2')
        restored=Store(tmp_path/'bounds.db')
        control=await restored.control()
        assert control['budget']==100 and control['min_spread']==2
        assert control['stress_drop']==3 and control['min_net']==.25
        for key,value in [('budget','100.01'),('min_spread','1.99')]:
            with pytest.raises(ValueError):await restored.set_control(key,value)
        assert (await restored.control())['budget']==100
    asyncio.run(go())
