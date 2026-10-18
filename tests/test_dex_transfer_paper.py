import asyncio
import copy
from types import SimpleNamespace as NS
import pytest
from app.market_signals import Scanner
from app.dex_transfer_paper import Service
from app.dex_transfer_source import KIND
from app.strategy_math import D

ASSET='0x'+'2'*40
STABLE='0x55d398326f99059ff775485246999027b3197955'
R=dict(chain_id=56,asset=ASSET,asset_decimals=6,stable=STABLE,stable_decimals=18,
       base='X',cex_venue='bybit',cex_symbol='X/USDT:USDT',mode='paired',
       notional_usd=100,native_symbol='BNB/USDT:USDT',native_decimals=18)


class Client:
    has={'fetchCurrencies':True,'fetchTradingFee':True};precisionMode=4
    def __init__(self,clock):
        self.clock=clock;self.bid=D('1.079');self.ask=D('1.08');self.depth=D(1000)
        self.markets={s:dict(symbol=s,base=s.split('/')[0],quote='USDT',spot=True,
            active=True,contract=False,taker=.001,precision={'amount':.000001},
            limits={'amount':{'min':.001},'cost':{'min':.01}}) for s in ('X/USDT','BNB/USDT')}
        def network(addr):
            return dict(network='BSC',id='BSC',deposit=True,withdraw=True,fee='.01',precision='.000001',
                        limits={'withdraw':{'min':'.02'},'deposit':{'min':'.001'}},
                        info={'chain':'BSC','contractAddress':addr,'withdrawPercentageFee':'0'})
        self.currencies={name:dict(code=name,networks={'BSC':network(addr)}) for name,addr in [('X',ASSET),('USDT',STABLE)]}
    async def fetch_currencies(self):return copy.deepcopy(self.currencies)
    async def fetch_trading_fee(self,symbol):return dict(symbol=symbol,taker=.001)
    async def fetch_order_book(self,symbol):
        bid,ask=(D(500),D('500.01')) if symbol=='BNB/USDT' else (self.bid,self.ask)
        return dict(symbol=symbol,timestamp=self.clock()*1000,bids=[[str(bid),str(self.depth)]],asks=[[str(ask),str(self.depth)]])
    async def create_order(self,*a,**kw):raise AssertionError('No orders in PAPER')
    async def withdraw(self,*a,**kw):raise AssertionError('No transfers in PAPER')


class Prices:
    def __init__(self,clock):self.clock=clock;self.token_price=D(1);self.calls=[];self.fail=False
    async def price(self,chain,sell,buy,amount):
        if self.fail:return dict(ok=False,reason='TEST_DEX_OUTAGE')
        self.calls.append((sell,buy,amount))
        qty=D(amount)/(D(10)**(6 if sell==ASSET else 18))
        out=qty*self.token_price if sell==ASSET else qty/self.token_price
        raw=int(out*D(10)**(18 if buy==STABLE else 6))
        return dict(ok=True,chain_id=chain,sell_token=sell,buy_token=buy,sell_amount_raw=str(amount),
                    buy_amount_raw=str(raw),ts=self.clock(),gas_price_raw='1000000',network_fee_raw='100000000000',
                    network_fee_usd_extra='0',token_metadata={side:{f:'0' for f in ('buyTaxBps','sellTaxBps','transferTaxBps')} for side in ('sellToken','buyToken')})


async def setup(tmp_path,deposit=True):
    now=[1000.];clock=lambda:now[0];c=Client(clock);provider=Prices(clock)
    feed=Scanner(tmp_path/'db',lambda *a:asyncio.sleep(0),clock=clock,venues=[],trade_venues={'bybit'},account_clients={'bybit':c})
    await feed.init();feed.clients={'bybit':c}
    await feed.paper.set_home('bybit',feed.trade_venues)
    if deposit:await feed.store.set_control('dex_deposit_fee_pct',0)
    paired=NS(scanner=NS(catalog={1:R}),paper=NS(source=NS(provider=provider,price_providers={})))
    service=Service(feed,paired)
    return feed,service,c,provider,now


async def next_stage(feed,now):
    p=feed.paper.state['position']
    now[0]=max(now[0],p['due'])
    await feed.paper.monitor(feed)


@pytest.mark.parametrize('direction',[0,1])
def test_both_directions_conserve_one_account_and_use_later_sale(tmp_path,direction):
    async def go():
        feed,service,c,prices,now=await setup(tmp_path)
        if direction==0:c.bid=D('.999');c.ask=D(1);prices.token_price=D('1.08')
        buy,sell=('bybit','dex56') if direction==0 else ('dex56','bybit')
        e=await service.review('X/USDT',buy,sell)
        assert not e['reasons'],e
        assert e['transfer']['kind']==KIND
        await feed.paper.open(dict(signal=e,transfer=e['transfer']))
        assert D(feed.paper.state['cash'])<225 and D(feed.paper.state['cash'])>224
        with pytest.raises(ValueError):await feed.paper.open(dict(signal=e,transfer=e['transfer']))
        # No credit or fill before the relevant modeled arrival time.
        before=copy.deepcopy(feed.paper.state);await feed.paper.monitor(feed)
        if direction:assert feed.paper.state==before
        for _ in range(5):
            if feed.paper.state['position']['status']=='BASE_IN_TRANSIT':break
            await next_stage(feed,now)
        p=feed.paper.state['position'];assert p['status']=='BASE_IN_TRANSIT',feed.paper.state
        entry_qty=p['remaining'];assert D(entry_qty)>0
        # Restart with exactly the same capital and reserved position.
        restored=type(feed.paper)(feed.store,feed.clock);await restored.init()
        feed.paper=restored;service.paper=restored;restored.dex_transfer=service
        assert restored.state==before or D(restored.state['cash'])<250
        if sell=='dex56':prices.token_price=D('.9')
        else:c.bid=D('.9');c.ask=D('.901')
        for _ in range(10):
            if not restored.positions:break
            await next_stage(feed,now)
        assert not restored.positions,restored.state
        p=restored.state['last_position'];assert D(p['net'])<0
        assert D(restored.state['realized'])==D(p['net'])
        assert D(restored.state['cash'])==D(250)+D(p['net'])
        assert len([f for f in p['fills'] if f['stage']=='BUY'])==1
        assert D(p['sold'])<=D(entry_qty)
        assert p['live_allowed'] is False
    asyncio.run(go())


def test_missing_deposit_parameter_never_becomes_zero_and_expires(tmp_path):
    async def go():
        feed,s,_,_,now=await setup(tmp_path,False)
        with pytest.raises(ValueError,match='комиссию ввода'):await s.review('X/USDT','dex56','bybit')
        await feed.store.set_control('dex_deposit_fee_pct',.1)
        e=await s.review('X/USDT','dex56','bybit')
        assert e['transfer']['network_evidence']['X']['deposit_fee']=='0.001'
        now[0]+=86400
        with pytest.raises(ValueError,match='комиссию ввода'):await s.review('X/USDT','dex56','bybit')
    asyncio.run(go())


@pytest.mark.parametrize('fault',['contract','network','withdraw','percent','minimum'])
def test_network_gate_prevents_wrong_token_and_unavailable_transfer(tmp_path,fault):
    async def go():
        feed,s,c,_,_=await setup(tmp_path)
        row=c.currencies['X']['networks']['BSC']
        if fault=='contract':row['info']['contractAddress']='0x'+'4'*40
        if fault=='network':row['info']['chain']='ETH'
        if fault=='withdraw':row['withdraw']=False
        if fault=='percent':row['info']['withdrawPercentageFee']='.01'
        if fault=='minimum':row['limits']['deposit']['min']=None
        with pytest.raises(ValueError):await s.review('X/USDT','dex56','bybit')
        assert not feed.paper.positions and D(feed.paper.state['cash'])==250
    asyncio.run(go())


def test_no_rebuy_during_dex_outage_or_pause_after_buy(tmp_path):
    async def go():
        feed,s,c,prices,now=await setup(tmp_path)
        e=await s.review('X/USDT','dex56','bybit');assert not e['reasons']
        await feed.paper.open(dict(signal=e,transfer=e['transfer']))
        await next_stage(feed,now);await next_stage(feed,now)
        assert feed.paper.state['position']['status']=='BASE_TRANSFER_PENDING'
        prices.fail=True
        await next_stage(feed,now)
        assert feed.paper.state['position']['status']=='BASE_TRANSFER_PENDING'
        assert len(feed.paper.state['position']['fills'])==1
        prices.fail=False;s.entry_enabled=feed.entry_enabled=False
        for _ in range(8):
            if not feed.paper.positions:break
            await next_stage(feed,now)
        assert not feed.paper.positions
        assert len([f for f in feed.paper.state['last_position']['fills'] if f['stage']=='BUY'])==1
    asyncio.run(go())


def test_abort_after_stable_funding_keeps_money_at_actual_location(tmp_path):
    async def go():
        feed,s,c,prices,now=await setup(tmp_path)
        e=await s.review('X/USDT','dex56','bybit')
        await feed.paper.open(dict(signal=e,transfer=e['transfer']))
        await next_stage(feed,now)
        assert feed.paper.state['position']['status']=='BUY_PENDING'
        c.bid=D('.99');c.ask=D(1)
        await next_stage(feed,now)
        assert not feed.paper.positions and feed.paper.state['last_position']['status']=='ABORTED_AFTER_FUNDING'
        assert D(feed.paper.state['balances']['dex56'])>24
        assert D(feed.paper.state['realized'])<0
        assert D(feed.paper.state['cash'])==250+D(feed.paper.state['realized'])
        assert not feed.paper.state['last_position']['fills']
    asyncio.run(go())


def test_partial_bybit_sales_and_manual_hold_do_not_duplicate_base(tmp_path):
    async def go():
        feed,s,c,_,now=await setup(tmp_path)
        e=await s.review('X/USDT','dex56','bybit');await feed.paper.open(dict(signal=e,transfer=e['transfer']))
        for _ in range(4):await next_stage(feed,now)
        p=feed.paper.state['position'];assert p['status']=='SELL_PENDING'
        await feed.paper.control(p['id'],'hold_sale');await next_stage(feed,now)
        assert feed.paper.state['position']['sold']=='0'
        await feed.paper.control(p['id'],'sell_now')
        c.depth=D(10);await next_stage(feed,now)
        first=D(feed.paper.state['position']['sold']);assert first==10
        for _ in range(10):
            if not feed.paper.positions:break
            await next_stage(feed,now)
        p=feed.paper.state['last_position']
        assert D(p['sold'])<25 and D(p['sold'])>24
        assert len([f for f in p['fills'] if f['stage']=='SELL'])==3
        assert D(p['net'])>0
    asyncio.run(go())


def test_ui_and_configuration_are_paper_only(tmp_path):
    from app.transfer_console import render
    async def go():
        feed,s,*_=await setup(tmp_path,False)
        text,keys=await render(feed,'sig:show',False)
        assert 'Bybit Spot ↔ DEX' in text
        text,keys=await render(feed,'sig:field:dex_deposit_fee_pct',False)
        assert 'не указан' in text and 'PAPER' in text
        assert not any('live' in (b.callback_data or '') for row in keys.inline_keyboard for b in row)
        with pytest.raises(ValueError):await feed.store.set_control('dex_deposit_fee_pct',-1)
    asyncio.run(go())


def test_auto_uses_same_gates_and_one_position_then_keeps_monitoring_disabled(tmp_path):
    async def go():
        feed,s,c,prices,now=await setup(tmp_path)
        await feed.store.set_control('mode','auto')
        # First direction fails economics; rotation still reaches inverse.
        await s.cycle();assert not feed.paper.positions
        await s.cycle();assert len(feed.paper.positions)==1
        ident=next(iter(feed.paper.positions))
        await s.cycle();assert list(feed.paper.positions)==[ident]
        await feed.store.toggle();s.entry_enabled=False;feed.entry_enabled=False
        before=copy.deepcopy(feed.paper.state)
        now[0]=feed.paper.state['position']['due'];await s.cycle()
        assert feed.paper.state['position']['status']=='BUY_PENDING'
        # No new buy on a paused pre-funding route. Operator may cancel it.
        await s.cycle();assert not feed.paper.state['position']['fills']
        await feed.paper.control(ident,'cancel_buy')
        assert not feed.paper.positions
        assert D(feed.paper.state['cash'])==250+D(feed.paper.state['realized'])
    asyncio.run(go())


def test_paused_mode_changed_stale_and_loss_limit_block_open(tmp_path):
    async def go():
        feed,s,c,prices,now=await setup(tmp_path)
        e=await s.review('X/USDT','dex56','bybit')
        s.paused=lambda:True
        with pytest.raises(ValueError,match='паузе'):await feed.paper.open(dict(signal=e,transfer=e['transfer']))
        s.paused=lambda:False
        await feed.store.set_control('mode','auto')
        with pytest.raises(ValueError,match='Режим'):await feed.paper.open(dict(signal=e,transfer=e['transfer']))
        await feed.store.set_control('mode','manual');now[0]+=4
        with pytest.raises(ValueError,match='новая'):await feed.paper.open(dict(signal=e,transfer=e['transfer']))
        e=await s.review('X/USDT','dex56','bybit');feed.paper.state['realized']='-2.5'
        with pytest.raises(ValueError,match='лимит убытка'):await feed.paper.open(dict(signal=e,transfer=e['transfer']))
        assert D(feed.paper.state['cash'])==250
    asyncio.run(go())


def test_future_deposit_fee_changes_are_used_at_receipt(tmp_path):
    async def go():
        feed,s,c,prices,now=await setup(tmp_path)
        e=await s.review('X/USDT','dex56','bybit')
        await feed.paper.open(dict(signal=e,transfer=e['transfer']))
        for _ in range(3):await next_stage(feed,now)
        p=feed.paper.state['position'];assert p['status']=='BASE_IN_TRANSIT'
        gross=D(p['base_transfer']['gross'])
        await feed.store.set_control('dex_deposit_fee_pct',.5)
        await next_stage(feed,now)
        assert D(feed.paper.state['position']['remaining'])==gross*D('.995')
    asyncio.run(go())


def test_network_read_failure_backs_off_without_exposing_private_error(tmp_path):
    async def go():
        feed,s,c,prices,now=await setup(tmp_path)
        calls=[]
        async def fail():calls.append(1);raise RuntimeError('sensitive transport detail')
        c.fetch_currencies=fail
        for _ in range(2):
            with pytest.raises(ValueError,match='Bybit: сети недоступны') as err:
                await s.networks(s.catalog()[0])
            assert 'sensitive' not in str(err.value)
        assert len(calls)==1
        now[0]+=31
        with pytest.raises(ValueError):await s.networks(s.catalog()[0])
        assert len(calls)==2
    asyncio.run(go())


def test_outage_after_usdt_credit_waits_without_losing_or_rebuying(tmp_path):
    async def go():
        feed,s,c,prices,now=await setup(tmp_path)
        e=await s.review('X/USDT','dex56','bybit')
        await feed.paper.open(dict(signal=e,transfer=e['transfer']));await next_stage(feed,now)
        before=copy.deepcopy(feed.paper.state);prices.fail=True
        await next_stage(feed,now)
        assert feed.paper.state['position']['status']=='BUY_PENDING'
        assert feed.paper.state['balances']==before['balances'] and not feed.paper.state['position']['fills']
        prices.fail=False;await next_stage(feed,now)
        assert feed.paper.state['position']['status']=='BASE_TRANSFER_PENDING'
    asyncio.run(go())


def test_slow_tax_warmup_after_funding_waits_for_fresh_quotes(tmp_path):
    async def go():
        feed,s,c,prices,now=await setup(tmp_path)
        e=await s.review('X/USDT','dex56','bybit')
        await feed.paper.open(dict(signal=e,transfer=e['transfer']));await next_stage(feed,now)
        original=prices.price
        async def slow(*args):
            q=await original(*args);now[0]+=4;return q
        prices.price=slow
        await next_stage(feed,now)
        assert feed.paper.state['position']['status']=='BUY_PENDING'
        assert 'новые цены' in feed.paper.state['last_error'] and not feed.paper.state['position']['fills']
        prices.price=original;await next_stage(feed,now)
        assert feed.paper.state['position']['status']=='BASE_TRANSFER_PENDING'
    asyncio.run(go())


def test_removed_or_inactive_spot_route_cannot_reuse_old_signal(tmp_path):
    async def go():
        feed,s,c,prices,now=await setup(tmp_path)
        assert s.route_for('X/USDT','dex56','bybit')
        c.markets['X/USDT']['active']=False
        with pytest.raises(ValueError,match='ROUTE_UNAVAILABLE'):await feed.review('X/USDT','dex56','bybit')
        with pytest.raises(ValueError,match='spot-рынок'):await s.book(dict(R,spot_symbol='X/USDT'))
    asyncio.run(go())


def test_unrelated_incomplete_network_does_not_poison_exact_route(tmp_path):
    async def go():
        feed,s,c,prices,now=await setup(tmp_path)
        c.currencies['OTHER']=dict(code='OTHER',networks={'ETH':dict(info={'withdrawPercentageFee':''})})
        e=await s.review('X/USDT','dex56','bybit')
        assert e['transfer']['network_evidence']['X']['contract']==ASSET
        assert not e['reasons']
    asyncio.run(go())
