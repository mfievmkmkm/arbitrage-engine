import asyncio
import json
import sqlite3
import time
from types import SimpleNamespace
import pytest
from app.db import Diary
from app.paper import PaperEngine
from app.funding_paper_source import History
from app.paired_paper_policy import plan, futures_plan
from app.paired_paper_profile import configure, migrate_toggles
from app.strategy_toggle_store import Store
from app.strategy_runtime import StrategyRuntime


def opportunity(**changes):
    now = time.time()
    return dict(symbol='X/USDT:USDT', buy='bybit', sell='mexc', notional=100,
                entry_buy=100, entry_sell=102, exit_buy=99.99, exit_sell=102.01,
                executable=2, exit_spread=2.02, fee_pct=.2, safety_pct=.1,
                base_qty=1, ts=now, decision_at=now, funding_known=True,
                funding_calendar=dict(long_next=now+100, short_next=now+100,
                                      long_interval=8, short_interval=8), **changes)


def test_partial_convergence_admission_rejects_costs_and_forecast_only():
    row = opportunity()
    assert futures_plan(row)['partial_net'] > .25
    assert row['paired_economics']['partial_convergence_net_usd'] > .25
    row['funding_calendar'] = None
    with pytest.raises(ValueError, match='CALENDAR'): futures_plan(row)
    with pytest.raises(ValueError, match='PARTIAL_NET'): plan(100, .5, .3)
    with pytest.raises(ValueError, match='PARTIAL_NET'): plan(100, 2, .3, .8)
    with pytest.raises(ValueError, match='EXTREME'): plan(100, 20, .3)


def test_market_rise_is_hedged_and_only_spread_convergence_pays(tmp_path):
    async def go():
        diary = Diary(tmp_path/'ff.db'); await diary.init()
        engine = PaperEngine(diary,250); engine.paired_mode = True
        async def history(p, until): return History(True,0,(),'NO_SETTLEMENT_DUE',until)
        engine.funding_history = history
        row = opportunity(); p = await engine.open(row)
        assert p and p.base_qty == 1 and engine.used_capital > 202
        # Both prices rise by $10: PnL is still just the trading cost.
        row.update(exit_buy=109.99, exit_sell=112.01, decision_at=time.time())
        assert not await engine.mark_and_exit([row])
        assert p.last_mark['gross'] == pytest.approx(-.02)
        assert p.current_net_usd < 0
        # Only relative convergence now earns NET, even at the higher level.
        row.update(exit_buy=110, exit_sell=110.2, decision_at=time.time())
        closed = await engine.mark_and_exit([row])
        assert len(closed)==1 and closed[0].current_net_usd > 1
        with sqlite3.connect(diary.path) as db:
            assert db.execute('SELECT close_reason FROM paper_positions').fetchone()[0]=='PARTIAL_CONVERGENCE_NET'
            assert json.loads(db.execute('SELECT paired_policy FROM paper_positions').fetchone()[0])['mechanism']=='TWO_LEG_CONVERGENCE'
    asyncio.run(go())


def test_unknown_funding_freezes_exit_then_restores_without_fabricated_income(tmp_path):
    async def go():
        diary=Diary(tmp_path/'ff.db'); await diary.init()
        engine=PaperEngine(diary,250); engine.paired_mode=True
        async def unknown(p,until):return History(False,0,(),'SETTLEMENT_MISSING',p.opened_at)
        engine.funding_history=unknown
        row=opportunity(); p=await engine.open(row)
        row.update(exit_buy=100,exit_sell=103.5,decision_at=time.time())
        assert not await engine.mark_and_exit([row])
        assert p.exit_pending and p.exit_pending['reason']=='NET_STOP'
        frozen_net=p.exit_pending['price_net']; frozen_ts=p.exit_pending['ts']
        restored=PaperEngine(diary,250); restored.paired_mode=True
        await restored.restore()
        assert restored.positions[p.id].exit_pending['ts']==frozen_ts
        assert restored.used_capital > 202
        async def verified(p,until):return History(True,-.15,(),'PUBLIC_HISTORY',until)
        restored.funding_history=verified
        closed=await restored.mark_and_exit([])
        assert len(closed)==1
        assert closed[0].current_net_usd==pytest.approx(frozen_net-.15)
        assert closed[0].last_mark['ts']==frozen_ts
        assert not restored.positions
        with sqlite3.connect(diary.path) as db:
            assert db.execute('SELECT closed_at FROM paper_positions').fetchone()[0]==frozen_ts
    asyncio.run(go())


def test_profile_is_paper_only_and_migration_preserves_operator_toggles(tmp_path):
    values=dict(LIVE_ENABLED='false',PAPER_CAPITAL_USD='50',NOTIONAL_USD='5')
    configure(values)
    assert values['PAPER_CAPITAL_USD']=='250' and values['NOTIONAL_USD']=='100'
    assert values['LIVE_ENABLED']=='false' and values['DEX_DUAL_PAPER_ENABLED']=='true'
    with pytest.raises(ValueError,match='PAPER_ONLY'):configure(dict(LIVE_ENABLED='true'))
    runtime=StrategyRuntime(); runtime.enabled.update(dex_channel=True,dex_bybit=True)
    path=tmp_path/'state'; store=Store(str(path)+'.strategies.json')
    migrate_toggles(path,runtime,store)
    assert runtime.enabled['futures_futures'] and runtime.enabled['dex_paired']
    assert not runtime.enabled['dex_channel'] and not runtime.enabled['spot_futures']
    runtime.enabled['futures_futures']=False
    migrate_toggles(path,runtime,store)
    assert not runtime.enabled['futures_futures']
    assert not store.load({})['dex_bybit']


def test_paired_dex_keeps_actual_inventory_and_has_no_directional_fallback(tmp_path):
    from test_dex_virtual_paper import source, ASSET
    from app.dex_paired_experiment import PairedSource
    from app.cex_dex_paper import Engine
    async def go():
        original,route,public,provider,now=source('paired')
        class Funding:
            async def get(self,*args):return SimpleNamespace(next_ts=1000000,interval_hours=8,rate=.0001)
        original.fs=Funding()
        public.currencies={'X':dict(code='X',networks={'ETH':dict(network='ETH',contract=ASSET)})}
        route['max_notional_usd']=6
        public.bid=1.3
        source2=PairedSource(original,[route])
        # An extreme apparent price gap cannot create a position.
        bad=await source2.entry(route)
        assert not bad['ok'] and bad['reason']=='PAIRED_REFERENCE_GAP_EXTREME'
        route.update(notional_usd=100,max_notional_usd=110)
        public.bid=1.04
        result=await source2.entry(route)
        assert result['ok'],result
        p=result['position']
        assert p['cex_side']=='sell' and p['virtual_asset_qty']>0
        assert p['virtual_mode']=='paired' and p['paired_route']==dict(route,slippage_bps=20)
        assert p['cex_identity']['asset']==ASSET and p['live_allowed'] is False
        engine=Engine(tmp_path/'dex.db',source2,capital=250,virtual=True,namespace='paired',clock=source2.clock)
        await engine.init(); await engine.cycle(route)
        assert len(engine.positions)==1
        assert engine.used_capital > 200
        again=Engine(tmp_path/'dex.db',source2,capital=250,virtual=True,namespace='paired',clock=source2.clock)
        await again.init()
        assert len(again.positions)==1
    asyncio.run(go())


def test_metadata_unavailable_is_distinct_from_contract_mismatch():
    from app.dex_cex_identity import Gate
    from test_dex_virtual_paper import source, ASSET
    async def go():
        s,r,c,_,now=source('paired')
        gate=Gate(s.public,{},s.clock)
        with pytest.raises(ValueError,match='METADATA_UNAVAILABLE'):await gate.verify(r)
        c.currencies={'X':dict(code='X',networks={'ETH':dict(contract='0x'+'3'*40)})}
        now[0]+=301
        # A metadata dictionary must still be loaded to distinguish mismatch.
        c.has=dict(c.has,fetchCurrencies=True)
        async def currencies():return c.currencies
        c.fetch_currencies=currencies
        with pytest.raises(ValueError,match='IDENTITY_UNVERIFIED'):await gate.verify(r)
        c.currencies['X']['networks']['ETH']['contract']=ASSET
        now[0]+=301
        assert (await gate.verify(r))['asset']==ASSET
    asyncio.run(go())


def test_fresh_native_books_cap_both_legs_and_preserve_saved_quantity(tmp_path):
    from test_dex_virtual_paper import Public
    from app.paired_futures_quotes import refresh
    async def go():
        a,b=Public(time.time),Public(time.time)
        from decimal import Decimal, ROUND_DOWN
        for c in (a,b):
            c.price_to_precision=lambda symbol,value:str(Decimal(value).quantize(Decimal('.0001'), rounding=ROUND_DOWN))
        a.bid=99.999; b.bid=102
        class Funding:
            async def get(self,*args):return SimpleNamespace(next_ts=(time.time()+1000)*1000,interval_hours=8,rate=.0001)
        scanner=SimpleNamespace(clients={'bybit':a,'mexc':b},notional=100,
            specs={v:{'X/USDT:USDT':SimpleNamespace(contract_size=1)} for v in ('bybit','mexc')},
            safety_buffer_pct=.1, funding=Funding(),hold_seconds=1200)
        row=await refresh(opportunity(),scanner)
        assert row['native_plan']['valid'] and row['base_qty']==pytest.approx(.98)
        assert row['base_qty']*row['entry_buy']<=100 and row['base_qty']*row['entry_sell']<=100
        assert row['funding_calendar'] and futures_plan(row)['partial_net']>.25
        diary=Diary(tmp_path/'ff.db');await diary.init()
        engine=PaperEngine(diary,250);engine.paired_mode=True
        p=await engine.open(row); assert p
        a.bid=90; b.bid=91
        mark=await refresh(row,scanner,p)
        assert mark['base_qty']==p.base_qty
        assert mark['exit_fee_usd']>=0 and mark['fee_source']=='CONSERVATIVE_PUBLIC_RATE_ASSUMPTION'
    asyncio.run(go())


def test_cooldown_uses_persisted_pair_results_across_restarts(tmp_path):
    from app.paired_paper_profile import futures_cooldown
    async def go():
        diary=Diary(tmp_path/'ff.db');await diary.init()
        now=time.time()
        with sqlite3.connect(diary.path) as db:
            db.execute("INSERT INTO paper_positions(symbol,status,current_net_usd,closed_at,paired_policy) VALUES('X','CLOSED',-1,?,'{}')", (now-5,))
        assert await futures_cooldown(diary.path,'X',now)=='PAIRED_ASSET_COOLDOWN'
        with sqlite3.connect(diary.path) as db:
            db.execute("INSERT INTO paper_positions(symbol,status,current_net_usd,closed_at,paired_policy) VALUES('X','CLOSED',-1,?,'{}')", (now-10,))
        assert await futures_cooldown(diary.path,'X',now)=='PAIRED_ASSET_LOSS_LIMIT'
        assert await futures_cooldown(diary.path,'Y',now) is None
        assert await futures_cooldown(diary.path,'X',(int(now//86400)+1)*86400+1) is None
    asyncio.run(go())
