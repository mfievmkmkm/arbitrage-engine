"""Controlled acceptance only. Never market-trade evidence or production PnL."""
import asyncio
import copy
import json
import sqlite3
from types import SimpleNamespace as NS
import pytest
from app.decision_journal import Journal
from app.asset_registry import Registry
from app.trading_controls import Controls
from app.paired_signal_intake import Intake


def test_strategy_a_controlled_open_close_is_atomic_with_audit_and_no_live_calls(tmp_path):
    from test_dex_virtual_paper import source,ASSET
    from app.dex_paired_experiment import PairedSource
    from app.cex_dex_paper import Engine
    async def run():
        original,route,public,provider,now=source('paired')
        original.fs=NS(get=lambda *args:asyncio.sleep(0,result=NS(next_ts=1000000,interval_hours=8,rate=.0001)))
        public.currencies={'X':dict(code='X',networks={'ETH':dict(network='ETH',contract=ASSET)})}
        route.update(notional_usd=100,max_notional_usd=110)
        path=tmp_path/'controlled.sqlite'
        s=PairedSource(original,[route]);s.journal=Journal(path,s.clock);await s.journal.init()
        s.operator_profile=lambda:dict(mode='auto',min_net_usd=.25,max_spread_pct=10)
        e=Engine(path,s,capital=250,virtual=True,namespace='paired',clock=s.clock)
        await e.init();await e.cycle(route)
        assert len(e.positions)==1
        p=next(iter(e.positions.values()));trace=p['trace_id']
        assert p['cex_side']=='sell' and p['virtual_asset_qty']==p['base_qty']
        assert p['live_allowed'] is False and p['execution_verified'] is False
        await e.update_management(p['id'],auto_profit=False,target_usd=.5)
        public.bid=1.002;now[0]+=10
        await e.cycle(entry_enabled=False)
        assert e.positions  # Profit exit was disabled, protective exits still exist.
        await e.request_exit(p['id'])
        closed=await e.cycle(entry_enabled=False)
        assert len(closed)==1 and closed[0]['net']>0
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT status FROM arb_candidates WHERE id=?',(trace,)).fetchone()[0]=='CLOSED'
            assert db.execute('SELECT COUNT(*) FROM arb_lifecycle WHERE event="DEX_PAPER_OPEN"').fetchone()[0]==1
            assert db.execute('SELECT COUNT(*) FROM arb_checks WHERE candidate_id=? AND stage="CLOSED"',(trace,)).fetchone()[0]==1
            assert db.execute('SELECT COUNT(*) FROM ledger').fetchone()[0]==1
        restored=Engine(path,s,capital=250,virtual=True,namespace='paired',clock=s.clock)
        await restored.init();assert not restored.positions
    asyncio.run(run())


def test_observe_b_does_not_open_dex_position_and_settings_are_independent(tmp_path):
    from test_dex_transfer_paper import setup
    async def run():
        feed,service,client,prices,now=await setup(tmp_path)
        await feed.store.set_control('mode','observe')
        controls=Controls(tmp_path/'controls')
        controls.update(mode='auto',min_net_usd=2)
        e=await service.review('X/USDT','dex56','bybit')
        assert not e['reasons'] and e['mode']=='observe'
        with pytest.raises(ValueError,match='Наблюдение'):
            await feed.paper.open(dict(signal=e,transfer=e['transfer']))
        assert not feed.paper.positions and feed.paper.state['cash']=='250'
        assert (await feed.store.control())['min_net']==.25
        assert controls.profile('paper')['min_net_usd']==2
        with sqlite3.connect(feed.store.path) as db:
            assert db.execute('SELECT COUNT(*) FROM arb_lifecycle').fetchone()[0]==0
    asyncio.run(run())


def test_registry_expires_and_refuses_contract_and_denomination_conflicts(tmp_path):
    async def run():
        now=[100.];reg=Registry(tmp_path/'db',lambda:now[0])
        route=dict(cex_venue='bybit',cex_symbol='X/USDT:USDT',base='X',chain_id=56,asset='0x'+'1'*40)
        proof=dict(source='TEST_EXACT_NETWORK',verified_at=100.,expires_at=400.)
        market=dict(base='X',contract=True,contractSize=.01)
        await reg.record(route,proof,market)
        with pytest.raises(ValueError,match='CONTRACT_CONFLICT'):
            await reg.record(dict(route,asset='0x'+'2'*40),proof,market)
        with pytest.raises(ValueError,match='DENOMINATION'):
            await reg.record(route,proof,dict(market,base='1000X'))
        now[0]=401
        with pytest.raises(ValueError,match='EXPIRED'):await reg.record(route,proof,market)
    asyncio.run(run())


def test_channel_routes_mexc_and_bybit_and_records_era_and_aligned_rejections(tmp_path):
    from test_dex_virtual_paper import ROUTE,ASSET
    async def run():
        priority=[]
        class Resolver:
            async def resolve_signal(self,venue,signal):return dict(ROUTE,cex_venue=venue)
        service=NS(scanner=NS(discovery=Resolver()),paper=NS(source=NS(routes=[])),prioritize=priority.append)
        intake=Intake(tmp_path/'db',None,service,clock=lambda:1000)
        agt=dict(id=1,timestamp=1000,text='SHORT? #X #ETHEREUM '+ASSET)
        result=await intake.accept(agt)
        assert result['ok'] and {r['cex_venue'] for r in priority}=={'mexc','bybit'}
        assert (await intake.accept(agt))['duplicate'] and len(priority)==2
        era=dict(id=2,timestamp=1000,text='LONG? #ERA #BSC 0x00312400303d02c323295f6E8b7309bc30FB6BcE')
        assert (await intake.accept(era))['reason']=='DEX_SHORT_OR_INVENTORY_UNAVAILABLE'
        aligned=dict(id=3,timestamp=1000,text='SHORT? #AGT Aligned in 93 sec')
        assert (await intake.accept(aligned))['reason']=='ALIGNED_IS_ANALYSIS_REPORT'
        stale=dict(agt,id=4,timestamp=900)
        assert (await intake.accept(stale))['reason']=='CHANNEL_SIGNAL_EXPIRED'
        with sqlite3.connect(tmp_path/'db') as db:
            assert db.execute('SELECT COUNT(*) FROM paired_channel_intake').fetchone()[0]==4
            assert db.execute('SELECT COUNT(*) FROM arb_candidates WHERE source="channel"').fetchone()[0]==4
    asyncio.run(run())


@pytest.mark.parametrize('symbol,dex_in,future_in,dex_out,future_out,gross',[
    ('AGT',.06692,.07280,.069650,.069400,6.13),
    ('ZCAT',.04954,.05226,.04998,.05068,2.02),
])
def test_screenshot_historical_prices_check_relative_pnl_only(symbol,dex_in,future_in,dex_out,future_out,gross):
    # Screenshot prices are NOT historical bid/ask, depth, tax or execution proof.
    # This verifies signs and both legs; executable-size E2E is the separate test.
    from app.cex_dex_paper import mark
    p=dict(forward=True,cex_side='sell',base_qty=1000,entry_cash=1000*dex_in,entry_price=future_in,
           entry_fees=.2,safety=.1,entry_gas=.01,entry_dex={},entry_cex={},asset_amount_raw='1000',cex_contracts=1000)
    x=dict(cash=1000*dex_out,price=future_out,fee_rate=.0008,gas=.01,ts=100,gas_price=1,gas_book={},dex={},cex={})
    h=NS(amount=0,verified=True,events=(),covered_until=100)
    m=mark(p,x,h)
    assert m['gross']==pytest.approx(gross)
    assert m['net']==pytest.approx(gross-.2-.1-1000*future_out*.0008-.01)


def test_journal_redacts_secrets_and_keeps_failure_values_across_restart(tmp_path):
    async def run():
        now=[1000.];path=tmp_path/'db';j=Journal(path,lambda:now[0])
        trace=await j.start('B','X/USDT','bybit','mexc','paper_auto',evidence={'api_key':'PRIVATE','secret':'PRIVATE'})
        await j.check(trace,'COSTS','FAIL','MIN_NET_CHECK',dict(observed=-.2,threshold=.25,unit='USD'))
        await j.finish(trace,'REJECTED','MIN_NET_CHECK')
        restored=Journal(path,lambda:now[0])
        text=await restored.trace(trace)
        assert '-0.2' in text and '0.25' in text
        with sqlite3.connect(path) as db:
            payload=db.execute('SELECT payload FROM arb_candidates').fetchone()[0]
            assert 'PRIVATE' not in payload and '***' in payload
        now[0]+=3601;await restored.no_trade_reports(2)
        with sqlite3.connect(path) as db:assert db.execute('SELECT COUNT(*) FROM arb_diagnostic_reports').fetchone()[0]==0
        await restored.no_trade_reports(1)
        # No candidates in that window: no invented no-trade statistics.
        assert (await restored.snapshot())['counts']==[]
    asyncio.run(run())


async def staged_pair(tmp_path):
    from test_dex_virtual_paper import source,ASSET
    from app.dex_paired_experiment import PairedSource
    from app.cex_dex_paper import Engine
    from app.paired_entry_execution import Executor
    original,route,public,provider,now=source('paired')
    original.fs=NS(get=lambda *a:asyncio.sleep(0,result=NS(next_ts=1000000,interval_hours=8,rate=.0001)))
    public.currencies={'X':dict(code='X',networks={'ETH':dict(network='ETH',contract=ASSET)})}
    public.depth=100
    original_book=public.fetch_order_book
    async def book(symbol,limit=20):
        b=await original_book(symbol,limit)
        if symbol.startswith('X/'):b['bids'][0][1]=public.depth
        return b
    public.fetch_order_book=book
    route.update(notional_usd=100,max_notional_usd=110)
    s=PairedSource(original,[route]);s.journal=Journal(tmp_path/'staged.sqlite',s.clock);await s.journal.init()
    s.operator_profile=lambda:dict(mode='auto',min_net_usd=.25,max_spread_pct=10)
    e=Engine(s.journal.path,s,capital=250,virtual=True,namespace='paired',clock=s.clock)
    await e.init();e.entry_executor=Executor(s,s.clock)
    return e,s,route,public,provider,now


def test_staged_pair_reserves_before_fill_then_hedges_later_and_closes(tmp_path):
    async def run():
        e,s,r,c,provider,now=await staged_pair(tmp_path)
        await e.cycle(r)
        p=next(iter(e.positions.values()))
        assert p['status']=='ENTRY_RESERVED' and p['dex_filled_qty']==p['hedged_base_qty']==0
        await e.cycle(entry_enabled=False)
        p=next(iter(e.positions.values()));assert p['status']=='HEDGE_PENDING' and p['dex_filled_qty']>0 and p['hedged_base_qty']==0
        now[0]+=1;await e.cycle(entry_enabled=False)
        p=next(iter(e.positions.values()));assert p['status']=='OPEN'
        assert p['dex_filled_qty']==p['hedged_base_qty']
        c.bid=1.002;now[0]+=1
        closed=await e.cycle(entry_enabled=False)
        assert len(closed)==1 and closed[0]['net']>0
        with sqlite3.connect(s.journal.path) as db:
            assert db.execute('SELECT COUNT(*) FROM arb_checks WHERE stage="EXECUTED"').fetchone()[0]==1
            assert db.execute('SELECT COUNT(*) FROM arb_checks WHERE stage="CLOSED"').fetchone()[0]==1
    asyncio.run(run())


def test_staged_partial_hedge_survives_restart_and_closes_residual_at_loss(tmp_path):
    from app.cex_dex_paper import Engine
    from app.paired_entry_execution import Executor
    async def run():
        e,s,r,c,provider,now=await staged_pair(tmp_path)
        await e.cycle(r);await e.cycle(entry_enabled=False)
        c.depth=5;now[0]+=1
        await e.cycle(entry_enabled=False)
        p=next(iter(e.positions.values()))
        assert p['hedged_base_qty']==5 and p['dex_filled_qty']>90 and p['status']=='HEDGE_PENDING'
        restored=Engine(s.journal.path,s,capital=250,virtual=True,namespace='paired',clock=s.clock)
        await restored.init();restored.entry_executor=Executor(s,s.clock)
        now[0]+=11;await restored.cycle(entry_enabled=False)
        assert next(iter(restored.positions.values()))['status']=='UNHEDGED_RECOVERY'
        await restored.cycle(entry_enabled=False)
        p=next(iter(restored.positions.values()))
        assert p['hedged_base_qty']==p['virtual_asset_qty']==5
        assert any(f['stage']=='DEX_UNHEDGED_UNWIND' for f in p['execution_events'])
        closed=await restored.cycle(entry_enabled=False)
        assert len(closed)==1 and closed[0]['net']<0
        fills=[f for f in closed[0]['execution_events'] if f['stage']=='CEX_SHORT']
        assert len(fills)==1 and fills[0]['base_qty']==5
        assert restored.state['realized_net']==closed[0]['net']
        with sqlite3.connect(s.journal.path) as db:assert db.execute('SELECT COUNT(*) FROM ledger').fetchone()[0]==1
    asyncio.run(run())


def test_staged_one_leg_quote_outage_never_fabricates_hedge_or_recovery(tmp_path):
    async def run():
        e,s,r,c,provider,now=await staged_pair(tmp_path)
        await e.cycle(r);await e.cycle(entry_enabled=False)
        c.depth=0;now[0]+=1;await e.cycle(entry_enabled=False)
        p=next(iter(e.positions.values()));assert p['hedged_base_qty']==0
        now[0]+=11;await e.cycle(entry_enabled=False)
        price=provider.price
        async def outage(*a,**kw):return dict(ok=False,reason='CONTROLLED_TEST_OUTAGE')
        provider.price=outage
        await e.cycle(entry_enabled=False)
        assert e.positions and e.used_capital>0
        p=next(iter(e.positions.values()));assert p['hedged_base_qty']==0 and p['status']=='UNHEDGED_RECOVERY'
        provider.price=price
        closed=await e.cycle(entry_enabled=False)
        assert len(closed)==1 and closed[0]['exit_reason']=='UNHEDGED_UNWIND' and closed[0]['net']<0
        assert not any(f['stage']=='CEX_SHORT' for f in closed[0]['execution_events'])
    asyncio.run(run())


def test_staged_cancel_before_first_fill_never_creates_trade_pnl(tmp_path):
    async def run():
        e,s,r,c,provider,now=await staged_pair(tmp_path)
        await e.cycle(r);p=next(iter(e.positions.values()))
        await e.request_exit(p['id']);await e.cycle(entry_enabled=False)
        assert not e.positions and e.state.get('closed_count',0)==0
        with sqlite3.connect(s.journal.path) as db:
            assert db.execute('SELECT COUNT(*) FROM ledger').fetchone()[0]==0
            assert db.execute('SELECT status FROM cex_dex_virtual_paper_paired').fetchone()[0]=='CANCELLED_BEFORE_FILL'
    asyncio.run(run())


@pytest.mark.parametrize('direction',[0,1])
def test_mexc_dex_transfer_uses_existing_bybit_capital_and_later_sale(tmp_path,direction):
    from test_dex_transfer_paper import setup,Client,STABLE
    from app.dex_transfer_router import Router
    from app.strategy_math import D
    async def run():
        feed,old,bybit,prices,now=await setup(tmp_path)
        mexc=Client(feed.clock)
        # Complete CONTROLLED network evidence. Real API omissions must reject.
        usdt_eth='0xdac17f958d2ee523a2206206994597c13d831ec7'
        for client in (bybit,mexc):
            for coin in client.currencies.values():
                for n in coin['networks'].values():n.update(deposit_fee='0',deposit_fee_percentage=False)
            n=copy.deepcopy(client.currencies['USDT']['networks']['BSC'])
            n.update(network='ETH',id='ETH',contract=usdt_eth)
            n['info']=dict(chain='ETH',contractAddress=usdt_eth,withdrawPercentageFee='0')
            client.currencies['USDT']['networks']['ETH']=n
        feed.clients['mexc']=mexc;feed.accounts.clients['mexc']=mexc;feed.trade_venues.add('mexc')
        router=Router(feed,old.paired);await router.init()
        buy,sell=('mexc','dex56') if direction else ('dex56','mexc')
        if direction:mexc.bid=D('.939');mexc.ask=D('.94')
        e=await feed.review('X/USDT',buy,sell)
        assert not e['reasons'],e['reasons']
        assert e['transfer']['source']=='bybit' and e['transfer']['route']['spot_venue']=='mexc'
        opened=await feed.paper.open(dict(signal=e,transfer=e['transfer']))
        assert D(feed.paper.state['balances']['bybit'])<D('225')
        for _ in range(8):
            p=feed.paper.state['position']
            if p['status']=='BASE_IN_TRANSIT':break
            now[0]=max(now[0]+1,p['due']+.01)
            await feed.paper.monitor(feed)
        assert feed.paper.state['position']['status']=='BASE_IN_TRANSIT'
        if sell=='mexc':mexc.bid=D('.9');mexc.ask=D('.901')
        else:prices.token_price=D('.8')
        for _ in range(12):
            p=feed.paper.state['position']
            if p is None:break
            now[0]=max(now[0]+1,p['due']+.01)
            await feed.paper.monitor(feed)
        assert not feed.paper.positions,feed.paper.state
        state=feed.paper.state;last=state['last_position']
        assert D(last['net'])<0
        assert D(state['cash'])==D(250)+D(state['realized'])
        assert sum((D(v) for v in state['balances'].values()),D(0))==D(state['cash'])
        assert len([f for f in last['fills'] if f['stage']=='BUY'])==1
        assert last['live_allowed'] is False
    asyncio.run(run())


def test_connected_mexc_spot_key_is_not_used_for_unsupported_futures_fee_endpoint():
    from test_dex_virtual_paper import source
    async def run():
        s,r,*_=source('paired');r['cex_venue']='mexc'
        class SpotOnly:
            has={'fetchTradingFee':True}
            async def fetch_trading_fee(self,symbol):raise AssertionError('SPOT API cannot quote futures fees')
        s.private['mexc']=SpotOnly()
        fee,evidence=await s.fee(r)
        assert fee==.0008 and evidence=='MEXC_API_FUTURES_PUBLIC_SCHEDULE_PAPER'
    asyncio.run(run())


def test_staged_hedge_does_not_reset_slippage_after_adverse_move(tmp_path):
    async def run():
        e,s,r,c,provider,now=await staged_pair(tmp_path)
        await e.cycle(r);await e.cycle(entry_enabled=False)
        c.bid=.50;now[0]+=1
        await e.cycle(entry_enabled=False)
        p=next(iter(e.positions.values()))
        assert p['hedged_base_qty']==0 and p['status']=='HEDGE_PENDING'
        now[0]+=11;await e.cycle(entry_enabled=False)
        closed=await e.cycle(entry_enabled=False)
        assert len(closed)==1 and closed[0]['exit_reason']=='UNHEDGED_UNWIND'
        assert not any(f['stage']=='CEX_SHORT' for f in closed[0]['execution_events'])
    asyncio.run(run())
