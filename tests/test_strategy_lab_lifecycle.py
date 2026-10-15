import asyncio
import copy
import json
import sqlite3
import pytest
from app.strategy_lab import Lab
from app.strategy_math import D, Invalid
from app.strategy_lab_examples import snapshot, book
from app.strategy_lab_view import render, import_jsonl


async def setup(tmp_path,key='perp_convergence',**kwargs):
    clock=[1000]
    lab=Lab(tmp_path/'lab.db',clock=lambda:clock[0],**kwargs)
    await lab.init()
    await lab.configure(mode='auto',enabled=[key])
    return lab,clock


def event(s=None,ident='e1'):
    return dict(id=ident,type='snapshot',ts=(s or {}).get('ts',1000),snapshot=s or snapshot())


def mark(key='perp_convergence',ts=1010):
    s=snapshot(key,ts)
    s['books'][0]['bids']=s['books'][0]['asks']=[['101','1000']]
    s['books'][1]['bids']=s['books'][1]['asks']=[['101.1','1000']]
    return s


def test_paired_net_fees_and_history_survive_restart(tmp_path):
    async def go():
        lab,clock=await setup(tmp_path)
        opened=await lab.process(event())
        p=lab.positions[opened['position_id']]
        assert D(p['last_mark']['net'])==D('-.1818')
        assert lab.reserved>D(180)
        out=await lab.process(event(mark(), 'e2'),now=1010)
        assert out['reason']=='NET_TARGET'
        expected=D('.9')*D('1.9')-D('.9')*(D(100)+102+D(101)+D('101.1'))*D('.0005')
        assert D(lab.state['realized'])==expected
        assert not lab.positions
        restored=Lab(lab.path);await restored.init()
        assert restored.state['realized']==lab.state['realized']
        assert (await restored.export())['state']['closed_count']==1
        with sqlite3.connect(lab.path) as db:
            assert db.execute('select status from strategy_lab_positions').fetchone()[0]=='CLOSED'
    asyncio.run(go())


def test_observation_manual_and_modes_do_not_abandon_monitoring(tmp_path):
    async def go():
        lab,clock=await setup(tmp_path)
        await lab.configure(mode='observe')
        assert (await lab.process(event()))['reason']=='LAB_OBSERVE_ONLY'
        await lab.configure(mode='manual')
        assert (await lab.process(event(ident='e2')))['reason']=='LAB_MANUAL_CONFIRM_REQUIRED'
        token=lab.issue('perp_convergence:synthetic-perp_convergence')
        async def refresh(_):return snapshot(ts=1001)
        clock[0]=1001
        out=await lab.confirm(token,refresh)
        assert out['reason']=='PAPER_OPEN'
        with pytest.raises(Invalid,match='EXPIRED'):
            await lab.confirm(token,refresh)
        await lab.configure(mode='observe',enabled=[])
        assert (await lab.process(event(mark(),'e3'),now=1010))['reason']=='NET_TARGET'
    asyncio.run(go())


def test_manual_requires_refresh_and_revision(tmp_path):
    async def go():
        lab,_=await setup(tmp_path)
        await lab.configure(mode='manual')
        await lab.process(event())
        token=lab.issue('perp_convergence:synthetic-perp_convergence')
        await lab.configure(values={'min_net':'.5'})
        async def refresh(_):return None
        with pytest.raises(Invalid,match='EXPIRED'):
            await lab.confirm(token,refresh)
        token=lab.issue('perp_convergence:synthetic-perp_convergence')
        with pytest.raises(Invalid,match='REFRESH_UNAVAILABLE'):
            await lab.confirm(token,refresh)
        assert not lab.positions
    asyncio.run(go())


def test_missing_funding_retains_reserve_then_late_ledger_closes(tmp_path):
    async def go():
        lab,_=await setup(tmp_path,'perp_funding')
        first=snapshot('perp_funding')
        pid=(await lab.process(event(first)))['position_id']
        s=mark('perp_funding',2200)
        # Forecast settlement at 1600 has not appeared in the ledger.
        out=await lab.process(event(s,'e2'),now=2200)
        assert out['reason']=='EXIT_ACCOUNTING_PENDING'
        assert lab.reserved>0
        await lab.process(dict(id='fund1',type='funding',position_id=pid,instrument='b',
                               settlement_ts=1600,cashflow='1.836',verified=True),now=2210)
        # Different ID cannot count the same settlement twice.
        with pytest.raises(Invalid,match='DUPLICATE'):
            await lab.process(dict(id='fund2',type='funding',position_id=pid,instrument='b',
                                   settlement_ts='1600.0',cashflow='1.836',verified=True),now=2210)
        frozen=lab.positions[pid]['exit_frozen']['price_net']
        s=mark('perp_funding',2220)
        # Changing later prices cannot replace the frozen exit model.
        s['books']=[]
        out=await lab.process(event(s,'e3'),now=2220)
        assert out['reason']=='EXIT_ACCOUNTING_COMPLETE'
        assert D(lab.state['realized'])==D(frozen)+D('1.836')
    asyncio.run(go())


def test_unknown_fill_blocks_entries_and_preserves_confirmed_fill(tmp_path):
    async def go():
        lab,_=await setup(tmp_path)
        await lab.configure(max_positions=2)
        s=snapshot();s['entry_fills']=[dict(instrument='a',status='final',quantity='.9',price='100',fee='.045'),
                                     dict(instrument='b',status='unknown')]
        pid=(await lab.process(event(s)))['position_id']
        assert lab.positions[pid]['status']=='ENTRY_UNCERTAIN'
        other=snapshot(ts=1001);other['route']='other'
        assert (await lab.process(event(other,'e2'),now=1001))['reason']=='LAB_UNHEDGED_POSITION'
        fills=[dict(instrument='a',status='final',quantity='.9',price='100',fee='.045'),
               dict(instrument='b',status='final',quantity='0',price='0',fee='0')]
        changed=copy.deepcopy(fills);changed[0]['quantity']='.8'
        with pytest.raises(Invalid,match='IMMUTABLE'):
            await lab.process(dict(id='bad',type='reconcile_entry',position_id=pid,fills=changed),now=1002)
        await lab.process(dict(id='ok',type='reconcile_entry',position_id=pid,fills=fills),now=1002)
        assert lab.positions[pid]['status']=='RECOVERY_REQUIRED'
        out=await lab.process(event(mark(),'e3'),now=1010)
        assert out['reason']=='UNHEDGED_RECOVERY'
        # One long only: 0.9*(101-100) minus its two actual/model fees.
        assert D(lab.state['realized'])==D('.80955')
    asyncio.run(go())


def test_inventory_no_phantom_profit_or_reuse_before_rebalance(tmp_path):
    async def go():
        lab,_=await setup(tmp_path,'spot_inventory')
        first=await lab.process(event(snapshot('spot_inventory')))
        assert first['reason']=='LAB_INVENTORY_LOW'
        await lab.configure(inventory=[dict(venue='b',asset_id='asset:COIN',quantity='1',price='102')])
        s=snapshot('spot_inventory',1001)
        pid=(await lab.process(event(s,'e2'),now=1001))['position_id']
        assert lab.positions[pid]['status']=='REBALANCE_PENDING'
        assert D(lab.state['realized'])==0
        assert lab.reserved>0
        await lab.process(dict(id='rb',type='rebalance',position_id=pid,quantity='.9',cost='.2',verified=True),now=1002)
        assert D(lab.state['realized'])==D('1.5091')
        with pytest.raises(Invalid,match='ALREADY_USED'):
            await lab.configure(inventory=[])
    asyncio.run(go())


def test_unused_inventory_also_encumbers_capital(tmp_path):
    async def go():
        lab,_=await setup(tmp_path)
        await lab.configure(inventory=[dict(venue='elsewhere',asset_id='asset:OTHER',quantity='1',price='200')])
        assert (await lab.process(event()))['reason']=='LAB_CAPITAL_LOW'
    asyncio.run(go())


def test_stale_monitor_cannot_close_or_release_capital(tmp_path):
    async def go():
        lab,_=await setup(tmp_path)
        pid=(await lab.process(event()))['position_id']
        old=copy.deepcopy(lab.positions[pid]['last_mark'])
        out=await lab.process(event(mark(ts=1010),'e2'),now=1030)
        assert out['reason']=='MONITOR_UNAVAILABLE'
        assert lab.positions[pid]['last_mark']==old and lab.reserved>0
    asyncio.run(go())


def test_idempotence_and_concurrent_writer_protection(tmp_path):
    async def go():
        lab,_=await setup(tmp_path)
        other=Lab(lab.path);await other.init()
        e=event();await lab.process(e)
        assert (await lab.process(e))['reason']=='DUPLICATE_EVENT'
        changed=copy.deepcopy(e);changed['snapshot']['quantity']='.8'
        with pytest.raises(Invalid,match='ID_REUSED'):
            await lab.process(changed)
        with pytest.raises(Invalid,match='CONCURRENT_WRITER'):
            await other.configure(mode='manual')
    asyncio.run(go())


def test_daily_stop_preserves_losses_and_cycle_snapshot_cannot_mint_twice(tmp_path):
    async def go():
        lab,_=await setup(tmp_path,'dex_atomic')
        await lab.configure(daily_loss='.5')
        s=snapshot('dex_atomic');s.update(execution_result='revert',revert_cost_verified=True,revert_gas_cost='.6')
        out=await lab.process(event(s))
        assert D(lab.state['realized'])==D('-.6')
        next_s=snapshot('dex_atomic',1001)
        assert (await lab.process(event(next_s,'e2'),now=1001))['reason']=='LAB_DAILY_STOP'
        assert lab.state['closed_count']==1
    asyncio.run(go())


def test_same_cycle_quote_is_single_use_even_with_different_event_ids(tmp_path):
    async def go():
        lab,_=await setup(tmp_path,'dex_atomic')
        s=snapshot('dex_atomic');await lab.process(event(s))
        assert (await lab.process(event(s,'e2')))['reason']=='LAB_ENTRY_SNAPSHOT_ALREADY_USED'
        assert lab.state['closed_count']==1
    asyncio.run(go())


@pytest.mark.parametrize('key',['dated_carry','options_parity'])
@pytest.mark.parametrize('terminal',['50','100','150'])
def test_expiry_settlement_matches_payoff_including_spot_index_risk(tmp_path,key,terminal):
    async def go():
        lab,_=await setup(tmp_path,key)
        s=snapshot(key);pid=(await lab.process(event(s)))['position_id']
        spot=book('a','spot',D(terminal)-D('.1'),2000)
        fees={'b':'0'} if key=='dated_carry' else {'p':'0','c':'0'}
        await lab.process(dict(id='settle',type='settlement',position_id=pid,verified=True,
                               expiry=2000,index_price=terminal,spot_books=[spot],settlement_fees=fees,
                               financing_cost='0',exit_extra_cost='0'),now=2000)
        expected=D('.9')*(D('1.9') if key=='dated_carry' else D('2.9'))
        expected-=D('.9')*(D(202) if key=='dated_carry' else D(107))*D('.0005')
        expected-=D('.9')*(D(terminal)-D('.1'))*D('.0005')
        assert D(lab.state['realized'])==expected
        assert not lab.positions
    asyncio.run(go())


def test_ui_catalog_bounds_and_import_fail_closed(tmp_path):
    async def go():
        lab,_=await setup(tmp_path)
        for key in ('home','catalog','risk','positions','options_parity'):
            text,keyboard=render(lab,key)
            assert len(text)<4096
            assert all(len(b.callback_data.encode())<=64 for row in keyboard.inline_keyboard for b in row)
        out=await import_jsonl(lab,json.dumps(event()),allow_entries=False)
        assert out[0]['reason']=='LAB_OPERATOR_GATE'
        assert not lab.positions
        with pytest.raises(Invalid,match='TIME_ORDER'):
            await import_jsonl(lab,'\n'.join(json.dumps(event(snapshot(ts=t),str(t))) for t in (1002,1001)))
    asyncio.run(go())


def test_triangle_uses_new_quotes_at_every_step_and_can_lose(tmp_path):
    async def go():
        lab,_=await setup(tmp_path,'triangle')
        s=snapshot('triangle');pid=(await lab.process(event(s)))['position_id']
        assert lab.positions[pid]['status']=='CYCLE_EXECUTING'
        assert lab.state['closed_count']==0
        second=copy.deepcopy(s['books'][1]);second['ts']=1001
        await lab.process(dict(id='step2',type='cycle_step',position_id=pid,book=second),now=1001)
        third=copy.deepcopy(s['books'][2]);third['ts']=1002
        third['bids']=third['asks']=[['4','1000']]
        out=await lab.process(dict(id='step3',type='cycle_step',position_id=pid,book=third),now=1002)
        assert out['reason']=='SEQUENTIAL_CYCLE_COMPLETE'
        assert D(lab.state['realized'])<0
        assert not lab.positions
    asyncio.run(go())


def test_triangle_unwind_returns_actual_start_currency_without_future_profit(tmp_path):
    async def go():
        lab,_=await setup(tmp_path,'triangle')
        s=snapshot('triangle');pid=(await lab.process(event(s)))['position_id']
        unwind=copy.deepcopy(s['books'][0]);unwind['ts']=1001
        unwind['bids']=[['9','1000']]
        out=await lab.process(dict(id='unwind',type='cycle_unwind',position_id=pid,book=unwind,side='sell'),now=1001)
        assert out['reason']=='CYCLE_UNWIND'
        assert D(lab.state['realized'])<0
        assert lab.state['closed_count']==1
    asyncio.run(go())


def test_manual_settings_change_during_refresh_cannot_open(tmp_path):
    async def go():
        lab,_=await setup(tmp_path)
        await lab.configure(mode='manual')
        await lab.process(event())
        token=lab.issue('perp_convergence:synthetic-perp_convergence')
        async def refresh(_):
            await lab.configure(mode='auto')
            return snapshot()
        with pytest.raises(Invalid,match='EXPIRED'):
            await lab.confirm(token,refresh)
        assert not lab.positions
    asyncio.run(go())


def test_vwap_with_repeating_decimal_survives_durable_mark(tmp_path):
    async def go():
        lab,_=await setup(tmp_path)
        s=snapshot();s['books'][0]['asks']=[['100','.4'],['100.01','1']]
        out=await lab.process(event(s))
        assert out['reason']=='PAPER_OPEN'
        p=lab.positions[out['position_id']]
        assert p['last_mark']['net'] is not None
        assert not p.get('data_error')
    asyncio.run(go())


def test_reverse_carry_can_trigger_exit_before_debt_repayment_but_not_finalize(tmp_path):
    async def go():
        lab,_=await setup(tmp_path,'reverse_carry')
        s=snapshot('reverse_carry');pid=(await lab.process(event(s)))['position_id']
        end=snapshot('reverse_carry',1010)
        end['books'][0]['bids']=end['books'][0]['asks']=[['101','1000']]
        end['books'][1]['bids']=end['books'][1]['asks']=[['101','1000']]
        end['borrow_accrued']='.001'
        out=await lab.process(event(end,'e2'),now=1010)
        assert out['reason']=='EXIT_ACCOUNTING_PENDING'
        assert lab.positions[pid]['exit_frozen']['reason']=='NET_TARGET'
        assert lab.reserved>0
        end=snapshot('reverse_carry',1020)
        end.update(books=[],borrow_repaid=True,borrow_accrued='.001',accounting_asof=1010)
        out=await lab.process(event(end,'e3'),now=1020)
        assert out['reason']=='EXIT_ACCOUNTING_COMPLETE'
        assert not lab.positions and D(lab.state['realized'])>0
    asyncio.run(go())


def test_calendar_near_expiry_does_not_leave_far_contract_unaccounted(tmp_path):
    async def go():
        lab,_=await setup(tmp_path,'calendar')
        s=snapshot('calendar');pid=(await lab.process(event(s)))['position_id']
        # Near expiry 3000; far expiry 6000. Near settles, far is bought back.
        far=copy.deepcopy(s['books'][1]);far.update(ts=3000)
        far['bids']=far['asks']=[['105','1000']]
        out=await lab.process(dict(id='settle',type='settlement',position_id=pid,verified=True,
                expiry=3000,index_price='104',index_prices={'a':'104'},spot_books=[],derivative_books=[far],
                settlement_fees={'a':'.01'},financing_cost='0',exit_extra_cost='0'),now=3000)
        assert out['reason']=='EXPIRY_SETTLEMENT'
        expected=D('.9')*((104-100)+(102-105))-D('.9')*D(202)*D('.0005')-D('.01')-D('.9')*105*D('.0005')
        assert D(lab.state['realized'])==expected
        assert not lab.positions
    asyncio.run(go())
