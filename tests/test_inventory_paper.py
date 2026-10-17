import asyncio
import copy
import json
import sqlite3
import zipfile
from types import SimpleNamespace
import pytest
from app.inventory_paper import Engine, policy, day
from app.inventory_public_source import Source, Service, USDT_CONTRACTS
from app.strategy_math import D, Invalid, plain


def book(venue,ts=1000,ask='100',bid='99.9',size='100'):
    return dict(venue=venue,asset='ETH',ts=str(ts),ask=ask,bid=bid,
                asks=[[ask,size]],bids=[[bid,size]],step='.001',min_qty='.001',min_notional='1',fee_rate='.001')


def snapshot(ts=1000,**changes):
    a,b=book('a',ts),book('b',ts,ask='106.1',bid='106')
    s=dict(asset='ETH',buy='a',sell='b',ts=str(ts),skew='0',identity_verified=True,
           books=[a,b],wallet_books={'a|ETH':a,'b|ETH':b},rebalance=dict(verified=True,ts=str(ts),
             base=dict(available=True,network='ETH',fee='.001',minimum='.001',delay_seconds='900'),
             quote=dict(available=True,network='TRC20',fee='.05',minimum='1',delay_seconds='900')))
    s.update(changes);return s


async def engine(tmp_path,values=None):
    clock=[1000.0];e=Engine(tmp_path/'inventory.db',values,clock=lambda:clock[0]);await e.init()
    s=snapshot();await e.seed([s]);await e.mark(s['wallet_books']);return e,s,clock


class TransferSource:
    available=True
    async def network_check(self,*args):return self.available
    async def book(self,venue,asset):return book(venue,ts=2000,ask='106.1' if venue=='b' else '100',bid='106' if venue=='b' else '99.9')


def run(fn):return asyncio.run(fn())


def test_inventory_both_fills_transfers_persist_no_double_credit(tmp_path):
    async def go():
        e,s,clock=await engine(tmp_path);initial=copy.deepcopy(e.state['balances']);events=[]
        async def notify(kind,p):events.append(kind)
        e.on_event=notify
        p=await e.execute(s,*[s['books'][i] for i in (0,1,0)])
        assert p['status']=='REBALANCE_PENDING' and e.state['realized']=='0'
        assert events==['OPEN'] and len(e.positions)==1
        assert D(e.state['balances']['a|USDT'])<D(initial['a|USDT'])
        with pytest.raises(Invalid,match='PENDING'):e.plan(s)
        restored=Engine(e.path,{'capital':'50'},clock=lambda:clock[0]);await restored.init()
        assert restored.p['capital']==250  # no redeploy resets or fake top ups
        clock[0]=1901;source=TransferSource();await restored.monitor(source)
        assert restored.state['closed']==1 and D(restored.state['realized'])>0 and not restored.positions
        assert D(restored.state['balances']['b|ETH'])==D(initial['b|ETH'])
        assert D(restored.state['balances']['a|USDT'])==D(initial['a|USDT'])
        balances=copy.deepcopy(restored.state['balances']);await restored.monitor(source)
        assert restored.state['balances']==balances and restored.state['closed']==1
        with sqlite3.connect(e.path) as db:
            p=json.loads(db.execute('SELECT payload FROM inventory_paper_trades').fetchone()[0])
        assert all(t['status']=='CREDITED' for t in p['transfers'])
        assert p['close_reason']=='REBALANCE_MODEL_COMPLETE'
    run(go)


@pytest.mark.parametrize('change,reason',[
    ('small','SPREAD_TOO_SMALL'),('extreme','SPREAD_ANOMALY'),('identity','IDENTITY'),
    ('network','NETWORK_UNKNOWN'),('stale','STALE'),('skew','SYNCHRONIZED'),
    ('depth','DEPTH'),('fees','NET_TOO_SMALL'),('minimum','TRANSFER_MINIMUM')])
def test_inventory_rejects_invalid_edges_without_spending(tmp_path,change,reason):
    async def go():
        e,s,_=await engine(tmp_path);balances=copy.deepcopy(e.state['balances'])
        if change=='small':s['books'][1].update(bid='104',bids=[['104','100']])
        if change=='extreme':s['books'][1].update(bid='130',bids=[['130','100']])
        if change=='identity':s['identity_verified']=False
        if change=='network':s['rebalance']={'verified':False,'reason':'INVENTORY_NETWORK_UNKNOWN'}
        if change=='stale':s['ts']='990'
        if change=='skew':s['skew']='2'
        if change=='depth':s['books'][1]['bids']=[['106','.001']]
        if change=='fees':s['rebalance']['quote']['fee']='20'
        if change=='minimum':s['rebalance']['base']['minimum']='1'
        with pytest.raises(Invalid,match=reason):e.plan(s)
        assert e.state['balances']==balances and not e.positions
    run(go)


def test_single_leg_failure_records_real_model_loss_and_no_transfer(tmp_path):
    async def go():
        e,s,_=await engine(tmp_path);b=book('b',bid='90',ask='91')
        p=await e.execute(s,s['books'][0],b,book('a',bid='99',ask='100'))
        assert p['status']=='CLOSED' and D(p['net'])<0 and not p['transfers']
        assert e.state['losses']==1 and D(e.state['realized'])<0
        assert any(f['side']=='recovery_sell' for f in p['fills'])
        assert D(e.state['daily'][day(1000)])==D(p['net'])
    run(go)


def test_partial_fill_recovers_only_unpaired_base_then_transfers_matched(tmp_path):
    async def go():
        e,s,_=await engine(tmp_path);b=book('b',ask='106.1',bid='106',size='.125')
        p=await e.execute(s,s['books'][0],b,s['books'][0])
        assert D(p['bought'])==D('.25') and D(p['matched'])==D('.125') and D(p['residual'])==0
        assert p['status']=='REBALANCE_PENDING'
        assert D(p['transfers'][0]['amount'])==D('.125')
        assert D(next(f['quantity'] for f in p['fills'] if f['side']=='recovery_sell'))==D('.125')
    run(go)


def test_tiny_matched_fill_unwinds_both_sides_instead_of_waiting_forever(tmp_path):
    async def go():
        e,s,clock=await engine(tmp_path);s['rebalance']['base']['minimum']='.2'
        b=book('b',ask='106.1',bid='106',size='.1')
        p=await e.execute(s,s['books'][0],b,s['books'][0]);assert p['status']=='RECOVERY_REQUIRED'
        clock[0]=2000;await e.monitor(TransferSource())
        assert not e.positions and e.state['closed']==1 and e.state['losses']==1
    run(go)


def test_failed_network_timeout_halts_without_fabricated_receipt(tmp_path):
    async def go():
        e,s,clock=await engine(tmp_path);await e.execute(s,s['books'][0],s['books'][1],s['books'][0])
        source=TransferSource();source.available=False;clock[0]=5000;await e.monitor(source)
        assert e.state['halted']=='INVENTORY_TRANSFER_OR_RECOVERY_TIMEOUT'
        assert e.state['realized']=='0' and all(t['status']=='IN_TRANSIT' for p in e.positions.values() for t in p['transfers'])
        source.available=True;clock[0]=5001;await e.monitor(source)
        assert not e.positions and e.state['closed']==1 and e.state['halted']
    run(go)


def test_portfolio_stop_counts_inventory_market_loss_and_stale_data_blocks(tmp_path):
    async def go():
        e,s,_=await engine(tmp_path)
        await e.mark({});assert e.state['valuation_error']
        with pytest.raises(Invalid,match='VALUATION'):e.plan(s)
        down={v+'|ETH':book(v,ask='91',bid='90') for v in ('a','b')}
        await e.mark(down)
        assert e.state['halted']=='INVENTORY_PORTFOLIO_STOP'
        assert D(e.state['valuation']['total_pnl'])<0 and e.state['realized']=='0'
    run(go)


def test_observe_cannot_execute_and_native_precision_is_joint(tmp_path):
    async def go():
        e,s,_=await engine(tmp_path)
        assert await e.execute(s,*[s['books'][i] for i in (0,1,0)],allow_entry=False) is None
        s['books'][1]['step']='.03';assert D(e.plan(s)['quantity'])==D('.24')
        with pytest.raises(Invalid):await e.configure(min_spread_pct='NaN')
        await e.configure(min_spread_pct='7.5');assert e.p['min_spread_pct']==D('7.5')
        assert not e.positions
    run(go)


def test_export_includes_inventory_state_trades_and_events(tmp_path):
    async def go():
        e,s,_=await engine(tmp_path);await e.execute(s,*[s['books'][i] for i in (0,1,0)])
        from app.compact_audit_export import build
        path=await build(e.path,tmp_path/'out')
        with zipfile.ZipFile(path) as z:
            assert {'inventory_paper_state.csv','inventory_paper_trades.csv','inventory_paper_events.csv'}<=set(z.namelist())
    run(go)


def network_row(asset):
    chain='TRC20' if asset=='USDT' else 'ETH'
    n=dict(network=chain,withdraw=True,deposit=True,active=True,percentage=False,fee='.001',
           limits=dict(withdraw=dict(min='.01'),deposit=dict(min='.001')))
    if asset=='USDT':n['contract']=USDT_CONTRACTS['TRC20']
    return dict(withdraw=True,deposit=True,networks={chain:n})


def test_public_network_gate_checks_identity_fees_status_age_and_no_order_methods():
    clock=[1000.0];source=Source({'a':object(),'b':object()},('a','b'),clock=lambda:clock[0])
    for v in ('a','b'):
        source.networks[v]={'ETH':network_row('ETH'),'USDT':network_row('USDT')};source.network_ts[v]=1000
    assert source.rebalance('ETH','a','b')['verified']
    source.networks['b']['USDT']['networks']['TRC20']['contract']='FAKE'
    assert not source.rebalance('ETH','a','b')['verified']
    source.networks['b']['USDT']=network_row('USDT')
    source.networks['b']['ETH']['networks']['ETH']['deposit']=False
    assert not source.rebalance('ETH','a','b')['verified']
    clock[0]=1500;assert not source.rebalance('ETH','a','b')['verified']
    with pytest.raises(Invalid,match='NATIVE_IDENTITY'):Source({},registry={'FAKE':dict(native=True,networks=['ETH'])})


def test_two_writers_cannot_duplicate_wallet_mutation(tmp_path):
    async def go():
        e,s,_=await engine(tmp_path);other=Engine(e.path,clock=e.clock);await other.init()
        await e.execute(s,*[s['books'][i] for i in (0,1,0)])
        with pytest.raises(Invalid,match='CONCURRENT_WRITER'):
            await other.execute(s,*[s['books'][i] for i in (0,1,0)])
        with sqlite3.connect(e.path) as db:assert db.execute('SELECT COUNT(*) FROM inventory_paper_trades').fetchone()[0]==1
    run(go)


def test_notification_failure_retries_after_restart_without_new_trade(tmp_path):
    async def go():
        e,s,clock=await engine(tmp_path)
        async def fail(*args):raise RuntimeError('Telegram unavailable')
        e.on_event=fail;await e.execute(s,*[s['books'][i] for i in (0,1,0)])
        assert len(e.state['notifications'])==1
        restored=Engine(e.path,clock=lambda:clock[0]);await restored.init();delivered=[]
        async def success(event,pos):delivered.append(event)
        restored.on_event=success;await restored.flush_notifications();await restored.flush_notifications()
        assert delivered==['OPEN'] and not restored.state['notifications'] and len(restored.positions)==1
    run(go)


def test_exchange_network_enrichment_preserves_unknowns_and_native_contracts():
    from app.inventory_network_metadata import enrich
    rows={'USDT':dict(networks={'TRC20':dict(id='TRX',info=dict(contractAddress=USDT_CONTRACTS['TRC20'],withdrawPercentageFee='0'))})}
    x=enrich(rows)['USDT']['networks']['TRC20']
    assert x['contract']==USDT_CONTRACTS['TRC20'] and x['percentage'] is False and x.get('fee') is None
    fees={'USDT':dict(info=dict(withdraw_percent='0%',withdraw_fix_on_chains={'TRX':'1'},withdraw_amount_mini='2'))}
    x=enrich(rows,fees)['USDT']['networks']['TRC20']
    assert x['fee']=='1' and x['limits']['withdraw']['min']=='2'
    assert 'limits' not in rows['USDT']['networks']['TRC20']
    fees['USDT']['info']['withdraw_percent']='1%'
    x=enrich(rows,fees)['USDT']['networks']['TRC20'];assert x['percentage'] is True


def test_public_capture_is_wired_to_wallet_and_monitors_when_entries_disabled(tmp_path):
    async def go():
        clock=[1000.0]
        class Client:
            has=dict(fetchCurrencies=True);precisionMode=4
            markets={'ETH/USDT':dict(spot=True,active=True,base='ETH',quote='USDT',taker=.001,
                precision=dict(amount=.001),limits=dict(amount=dict(min=.001),cost=dict(min=1)))}
            def __init__(self,bid,ask):self.bid,self.ask=bid,ask
            async def fetch_currencies(self):return {'ETH':network_row('ETH'),'USDT':network_row('USDT')}
            async def fetch_order_book(self,*args,**kwargs):
                return dict(timestamp=clock[0]*1000,bids=[[self.bid,100]],asks=[[self.ask,100]])
        clients={'a':Client(99.9,100),'b':Client(106,106.1)}
        source=Source(clients,('a','b'),registry={'ETH':dict(native=True,networks=['ETH'])},clock=lambda:clock[0])
        e=Engine(tmp_path/'integrated.db',clock=lambda:clock[0]);await e.init()
        service=Service(source,e,allowed=lambda symbol=None:True)
        service.entry_enabled=False;await service.cycle()
        assert e.state['initialized'] and not e.positions
        service.entry_enabled=True;await service.cycle()
        assert len(e.positions)==1 and e.state['realized']=='0'
        service.entry_enabled=False;clock[0]=1901;await service.cycle()
        assert not e.positions and e.state['closed']==1 and D(e.state['realized'])>0
    run(go)


def test_experiment_migration_is_once_only_and_does_not_touch_live(tmp_path):
    from app.inventory_experiment import migrate,render,keyboard
    from app.strategy_toggle_store import Store
    from app.strategy_runtime import StrategyRuntime
    from app.trading_controls import Controls
    controls=Controls(tmp_path/'controls');before=controls.profile('live');runtime=StrategyRuntime();store=Store(str(tmp_path/'toggles'))
    migrate(tmp_path/'state',runtime,store,controls)
    assert runtime.enabled['spot_inventory'] and not runtime.enabled['futures_futures']
    assert controls.profile('live')==before
    runtime.enabled['spot_inventory']=False;migrate(tmp_path/'state',runtime,store,controls)
    assert not runtime.enabled['spot_inventory'] and controls.profile('live')==before
    async def go():
        e,s,_=await engine(tmp_path);text=render(e)
        assert len(text)<4096 and 'Все деньги виртуальные' in text
        assert any(b.callback_data=='inv:card' for r in keyboard().inline_keyboard for b in r)
    run(go)


def test_unhedged_timeout_keeps_recovery_and_accelerates_monitoring(tmp_path):
    async def go():
        e,s,clock=await engine(tmp_path)
        small=book('a',size='.01');p=await e.execute(s,s['books'][0],book('b',ask='91',bid='90'),small)
        assert D(p['residual'])>0
        class NoDepth(TransferSource):
            async def book(self,*args):
                b=book('a',ts=1012);b['bids']=[];return b
        service=Service(NoDepth(),e);assert service.interval==2
        clock[0]=1012;await e.monitor(service.source)
        assert e.state['halted']=='INVENTORY_UNHEDGED_TIMEOUT' and len(e.positions)==1 and e.state['closed']==0
    run(go)


def test_raw_percentage_fee_cannot_be_hidden_by_unified_fixed_fee():
    from app.inventory_network_metadata import enrich
    rows={'USDT':dict(networks={'TRC20':dict(info=dict(withdrawPercentageFee='1'))})}
    fees={'USDT':dict(networks={'TRC20':dict(withdraw=dict(fee=1,percentage=False))})}
    assert enrich(rows,fees)['USDT']['networks']['TRC20']['percentage'] is True
