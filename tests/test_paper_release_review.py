import asyncio
import json
import sqlite3
import time
from decimal import Decimal, ROUND_DOWN
from types import SimpleNamespace as NS
import pytest
from app.db import Diary
from app.paper import PaperEngine
from app.risk import RiskGuard
from app.strategy_runtime import StrategyRuntime
from app.paper_console import render, summary, main_menu, keyboard, positions
from app.paired_paper_profile import require_monitoring, load_pause, save_pause


def test_release_menu_reports_new_net_and_unknown_funding_without_old_profit(tmp_path,monkeypatch):
    async def go():
        path=tmp_path/'db';diary=Diary(path);await diary.init()
        with sqlite3.connect(path) as db:
            for value in (-1,.5,-.25,2):
                db.execute("INSERT INTO paper_positions(status,current_net_usd,closed_at,paired_policy) VALUES('CLOSED',?,?, '{}')",(value,time.time()))
            db.execute("INSERT INTO paper_positions(status,current_net_usd) VALUES('CLOSED',-100)")
            db.execute("INSERT INTO signal_decisions(strategy,reason,payload) VALUES('futures_futures','BAD','broken-json')")
            db.execute("INSERT INTO signal_decisions(strategy,reason,payload) VALUES('futures_futures','PAIRED_PARTIAL_NET_LOW','{\"paired_paper\":true}')")
        engine=PaperEngine(diary,250)
        scanner=NS(clients={'bybit':object(),'mexc':object()},ids=['bybit','mexc'],paused=False,notional=100)
        dex=NS(state=dict(closed_count=1,realized_net=-.5),positions={},budget=lambda:249.5)
        bundle=NS(dex_paired=dex,dex_experiments={},dex_paper=None,sf_paper=NS(positions={}),ss_paper=None,funding_paper=None)
        runtime=StrategyRuntime();runtime.enabled['dex_paired']=True
        stats=await summary(path,dex)
        assert stats['net']==1.25 and stats['drawdown']==1 and stats['win_avg']==1.25
        assert stats['loss_avg']==-.625 and len(stats['skips'])==1
        # Exercise actual routing, not just a standalone renderer.
        import app.main as main
        monkeypatch.setattr(main,'paired_paper',True);monkeypatch.setattr(main,'secondary',bundle)
        monkeypatch.setattr(main,'paper',engine);monkeypatch.setattr(main,'scanner',scanner)
        monkeypatch.setattr(main,'risk',RiskGuard(250));monkeypatch.setattr(main,'strategy_runtime',runtime)
        monkeypatch.setattr(main,'config',NS(db_path=path,runtime_state_path=tmp_path/'state'))
        for screen in ('home','paper','strategy_stats','diary','paper_settings','paper_checks','paper_modes_more','paper_archive','strategies'):
            text=await main.text_for(screen)
            assert 0<len(text)<=4096 and main.keyboard_for(screen) is not None
        home=await main.text_for('home')
        assert '+0.7500' in home and '-100' not in home
        callbacks={b.callback_data for row in main.menu().inline_keyboard for b in row}
        assert len(callbacks)==6 and 'balances:show' in callbacks
        assert 'live_stop' not in callbacks and 'live' not in callbacks
        class Query:
            from_user=NS(id=1);message=object();data='pause'
            async def answer(self,*args,**kwargs):pass
        async def edit(*args,**kwargs):pass
        monkeypatch.setattr(main,'allowed',lambda _:True);monkeypatch.setattr(main,'safe_edit',edit)
        await main.callbacks(Query())
        assert scanner.paused and load_pause(tmp_path/'state')
    asyncio.run(go())


def test_open_positions_cannot_be_abandoned_by_env_change(tmp_path):
    async def go():
        path=tmp_path/'db';diary=Diary(path);await diary.init()
        await require_monitoring(path,False)
        with sqlite3.connect(path) as db:
            db.execute("INSERT INTO paper_positions(status,paired_policy) VALUES('OPEN','{}')")
        with pytest.raises(ValueError,match='FUTURES'):await require_monitoring(path,False)
        await require_monitoring(path,True)
        with sqlite3.connect(path) as db:
            db.execute("UPDATE paper_positions SET status='CLOSED'")
            db.execute('CREATE TABLE cex_dex_virtual_paper_paired(status TEXT)')
            db.execute("INSERT INTO cex_dex_virtual_paper_paired VALUES('EXIT_ACCOUNTING_PENDING')")
        with pytest.raises(ValueError,match='DEX'):await require_monitoring(path,False)
    asyncio.run(go())


def test_dex_requotes_native_size_and_writes_off_small_surplus_without_refund():
    from test_dex_virtual_paper import source, ASSET, STABLE
    from app.dex_paired_experiment import PairedSource
    async def go():
        original,r,c,_,now=source('paired')
        r.update(asset_decimals=6,notional_usd=100,max_notional_usd=110)
        original_market=c.market
        def market(symbol):
            result=original_market(symbol);result['precision']['amount']=1
            return result
        c.market=market;c.amount_to_precision=lambda symbol,value:str(Decimal(str(value)).quantize(Decimal('1'),rounding=ROUND_DOWN))
        c.bid=1.08;c.currencies={'X':dict(code='X',networks={'ETH':dict(contract=ASSET)})}
        class Funding:
            async def get(self,*args):return NS(next_ts=1000000,interval_hours=8,rate=.0001)
        original.fs=Funding()
        class Prices:
            async def price(self,chain,sell,buy,amount):
                coefficient=Decimal('.9645678')
                output=Decimal(amount)*coefficient if sell==STABLE else Decimal(amount)/coefficient
                return dict(ok=True,chain_id=chain,sell_token=sell,buy_token=buy,sell_amount_raw=str(amount),
                    buy_amount_raw=str(int(output)),ts=now[0],network_fee_raw='1000000000000',
                    token_metadata={side:dict(buyTaxBps='0',sellTaxBps='0',transferTaxBps='0') for side in ('buyToken','sellToken')})
        original.provider=Prices();paired=PairedSource(original,[r])
        result=await paired.entry(r);assert result['ok'],result
        p=result['position']
        assert len(p['native_size_attempts'])>=2 and p['entry_cash']<100
        assert p['base_qty']==p['virtual_asset_qty']==96
        assert int(p['asset_amount_raw'])==96000000 and int(p['rounding_discard_raw'])>0
        assert p['hedge_residual_qty']==0 and p['live_allowed'] is False
        assert 'no refund credited' in p['assumptions'][-1]
        assert p['entry_cash']==p['native_size_attempts'][-1]['notional_usd']
    asyncio.run(go())


def test_disabled_idle_modules_sleep_but_pending_positions_keep_monitoring():
    from app.secondary_strategy_runtime import SecondaryRuntime
    async def go():
        watched=[]
        class Service:
            idle_when_disabled=True;interval=.005
            def __init__(self,positions):self.paper=NS(positions=positions)
            async def cycle(self):
                if not self.paper.positions:raise AssertionError('Idle module must not scan')
                watched.append(self.entry_enabled);return []
        runtime=StrategyRuntime();runtime.enabled.update(idle=False,pending=False)
        async def record(*args):pass
        services=SecondaryRuntime(runtime,record,'unused',.005)
        services.add('idle',Service({}));services.add('pending',Service({'1':{'status':'EXIT_ACCOUNTING_PENDING'}}))
        await services.start();await asyncio.sleep(.03);await services.stop()
        assert watched and not any(watched)
    asyncio.run(go())


def test_corrupted_calendar_never_credits_zero_as_verified():
    from app.paired_futures_funding import Settlements
    async def go():
        settlements=Settlements({},None,100)
        p=NS(opened_at=100,funding_calendar=dict(long_next=110,short_next=110,long_interval=0,short_interval=8))
        assert not (await settlements(p,105)).verified
        p.funding_calendar['long_interval']=8
        assert (await settlements(p,105)).verified
        assert not (await settlements(p,99)).verified
    asyncio.run(go())
