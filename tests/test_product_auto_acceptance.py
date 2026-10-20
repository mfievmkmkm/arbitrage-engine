"""Controlled acceptance of actual auto-dispatch, not production market trades."""
import asyncio
import sqlite3
from types import SimpleNamespace as NS
import pytest


def test_a_scanner_ready_auto_dispatch_two_fills_and_close(tmp_path,monkeypatch):
    from test_final_spec_acceptance import staged_pair
    from app.dex_paired_experiment import PairedCycle
    from app.dex_parallel_scanner import Scanner
    from app.trading_controls import Controls
    from app.risk import RiskGuard
    import app.main as main
    async def go():
        engine,source,route,client,_,now=await staged_pair(tmp_path)
        controls=Controls(tmp_path/'controls',clock=source.clock)
        controls.update(mode='auto')
        source.operator_profile=lambda:controls.profile('paper')
        source.private['bybit']=NS(has={'fetchTradingFee':True},
            fetch_trading_fee=lambda symbol:asyncio.sleep(0,result=dict(symbol=symbol,taker=.00055)))
        engine.operator_notional=lambda:controls.profile('paper')['notional_usd']
        service=PairedCycle(engine,{})
        service.scanner=Scanner(service)
        service.auto_enabled=lambda:controls.profile('paper')['mode']=='auto'
        runtime=NS(enabled={'dex_paired':True,'product_dex_paired':True})
        monkeypatch.setattr(main,'paired_paper',True)
        monkeypatch.setattr(main,'secondary',NS(inventory_paper=object(),dex_paired=engine))
        monkeypatch.setattr(main,'strategy_runtime',runtime)
        monkeypatch.setattr(main,'trading_controls',controls)
        monkeypatch.setattr(main,'risk',RiskGuard(250))
        monkeypatch.setattr(main,'paper',NS(positions={}))
        engine.allow_open=lambda p:main.operator_paper_reason('dex_paired',p) is None
        engine.entry_block_reason=lambda p:main.operator_paper_reason('dex_paired',p)
        candidate,reason=await service.scanner.check(route)
        assert candidate and reason=='READY'
        assert not engine.positions
        await service.cycle()
        assert next(iter(engine.positions.values()))['status']=='ENTRY_RESERVED'
        await service.cycle()
        assert next(iter(engine.positions.values()))['status']=='HEDGE_PENDING'
        now[0]+=1;await service.cycle()
        p=next(iter(engine.positions.values()))
        assert p['status']=='OPEN' and p['dex_filled_qty']==p['hedged_base_qty']
        client.bid=1.002;now[0]+=1
        await service.cycle()
        assert not engine.positions and engine.state['realized_net']>0
        with sqlite3.connect(source.journal.path) as db:
            events={x[0] for x in db.execute('SELECT event FROM arb_lifecycle')}
        assert {'ENTRY_RESERVED','PAIR_HEDGED'}<=events
    asyncio.run(go())


@pytest.mark.parametrize('direction',[0,1])
def test_b_router_auto_discovers_opens_waits_sells_later_and_closes(tmp_path,direction):
    from test_dex_transfer_paper import setup
    from app.dex_transfer_router import Router
    from app.strategy_math import D
    async def go():
        feed,original,c,prices,now=await setup(tmp_path)
        await feed.store.set_control('mode','auto')
        await feed.store.set_control('budget',100)
        await feed.store.set_control('min_spread',2)
        if direction==0:c.bid=D('.999');c.ask=D(1);prices.token_price=D('1.08')
        router=Router(feed,original.paired)
        router.handlers['bybit'].index=direction
        await router.cycle()
        assert feed.paper.state['position']
        assert feed.paper.state['position']['status'] in ('BUY_PENDING','USDT_TO_BUY_IN_TRANSIT')
        # Let the BUY complete, then change market before the transfer arrives.
        for _ in range(8):
            p=feed.paper.state['position']
            if p['status']=='BASE_IN_TRANSIT':break
            now[0]=max(now[0],p['due']);await router.cycle()
        assert feed.paper.state['position']['status']=='BASE_IN_TRANSIT'
        before=feed.paper.state['position']['sold']
        await router.cycle()
        assert feed.paper.state['position']['sold']==before
        if direction==0:prices.token_price=D('.90')
        else:c.bid=D('.90')
        feed.entry_enabled=False  # existing exposure must still be monitored
        for _ in range(8):
            p=feed.paper.state.get('position')
            if not p:break
            now[0]=max(now[0],p['due']);await router.cycle()
        assert not feed.paper.state['position']
        assert feed.paper.state['closed']==1 and D(feed.paper.state['realized'])<0
        with sqlite3.connect(feed.journal.path) as db:
            assert db.execute('SELECT COUNT(*) FROM arb_lifecycle WHERE strategy="B"').fetchone()[0]>0
    asyncio.run(go())


@pytest.mark.parametrize('change',[{'mode':'observe'},{'mode':'manual'},{'budget':50}])
def test_b_changed_entry_intent_cannot_buy_after_reservation(tmp_path,change):
    from test_dex_transfer_paper import setup
    async def go():
        feed,s,_,_,now=await setup(tmp_path)
        e=await s.review('X/USDT','dex56','bybit')
        await feed.paper.open(dict(signal=e,transfer=e['transfer']))
        p=feed.paper.state['position']
        now[0]=p['due'];await feed.paper.monitor(feed)
        assert feed.paper.state['position']['status']=='BUY_PENDING'
        for key,value in change.items():await feed.store.set_control(key,value)
        await feed.paper.monitor(feed)
        assert not feed.paper.state['position']
        last=feed.paper.state['last_position']
        assert last['status']=='CANCELLED_BEFORE_BUY' and not last['fills']
        assert last['reason']=='ENTRY_INTENT_CHANGED_BEFORE_BUY'
    asyncio.run(go())
