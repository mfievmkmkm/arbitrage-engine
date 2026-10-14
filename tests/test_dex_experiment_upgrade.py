import asyncio
from types import SimpleNamespace
import pytest
from app.cex_dex_paper import Engine
from app.dex_paper_experiments import build, render_mode
from app.secondary_strategy_runtime import SecondaryRuntime
from app.strategy_runtime import StrategyRuntime
from app.dex_signal_focus import parse_signal
from app.dex_route_discovery import Discovery
from app.build_virtual_watchlist import USDC, SEEDS


def test_virtual_topup_is_once_and_preserves_net_positions_and_history(tmp_path):
    from test_dex_virtual_paper import source
    async def go():
        s,r,public,provider,now = source("reference")
        s.routes.append(dict(r,cex_venue="mexc"));s.public["mexc"]=public
        db=tmp_path/"db"
        old=Engine(db,s,virtual=True,clock=s.clock);await old.init()
        runtime=StrategyRuntime();bundle=SimpleNamespace(dex_paper=old,runtime=SecondaryRuntime(runtime,None,db))
        async def notify(*args):pass
        engines=await build(bundle,runtime,str(db),str(tmp_path/"state"),notify,lambda p:True,capital=50,notional=20, strategy_version=1)
        runtime.enabled['dex_bybit']=True
        e=engines['dex_bybit'];await e.cycle(e.source.routes[0]);now[0]+=5;await e.cycle(e.source.routes[0])
        assert e.positions
        original=dict(next(iter(e.positions.values())))
        upgraded=await build(bundle,runtime,str(db),str(tmp_path/"state"),notify,lambda p:True, strategy_version=1)
        e=upgraded['dex_bybit']
        assert e.capital==250 and e.budget()==250 and len(e.state['capital_changes'])==1
        assert next(iter(e.positions.values()))['base_qty']==original['base_qty']
        assert e.source.routes[0]['notional_usd']==100
        again=await build(bundle,runtime,str(db),str(tmp_path/"state"),notify,lambda p:True, strategy_version=1)
        assert len(again['dex_bybit'].state['capital_changes'])==1
        view=render_mode('dex_bybit',e,True)
        assert '$250' in view and '$100' in view and 'Основные причины' in view
        assert 'Добавленные $200 не считаются прибылью' in view
        public.bid=1;now[0]+=5
        closed=await e.cycle(None,False)
        assert closed and e.budget()>250
    asyncio.run(go())


def test_larger_size_respects_channel_limit_and_notifies_only_committed_open(tmp_path):
    from test_dex_virtual_paper import source
    from app.dex_signal_focus import Signals
    from app.cex_dex_paper import Cycle
    async def go():
        s,r,public,provider,now=source('reference')
        r.update(cex_venue='mexc',notional_usd=50,max_notional_usd=50)
        s.public['mexc']=public;s.focus=True;s.signals_only=True
        s.signals=Signals(str(tmp_path/'signals'),s.clock)
        s.signals.add(f'SHORT? #X\nMax Size: 50 $X ($43)\n#ETHEREUM\n{r["asset"]}',[r],now[0],'channel')
        engine=Engine(tmp_path/'large.db',s,virtual=True,namespace='channel',capital=250,clock=s.clock)
        await engine.init();calls=[]
        async def opened(p):calls.append(p)
        engine.on_opened=opened
        cycle=Cycle(engine,[r]);await cycle.cycle();assert not calls
        now[0]+=5;await cycle.cycle()
        assert len(calls)==1
        p=calls[0]
        assert p['entry_cash']==43 and p['base_qty']*p['entry_price']<=43
        now[0]+=5;await cycle.cycle();assert len(calls)==1
    asyncio.run(go())


def test_channel_size_cap_parser():
    text=f'SHORT? #OTC\nMax Size: 7K $OTC ($43)\n#SOLANA\n{SEEDS["OTC"]}'
    assert parse_signal(text)['max_notional_usd']==43
    assert parse_signal(text.replace('($43)','($2K)'))['max_notional_usd']==2000


def test_discovery_exact_mint_scope_cache_and_channel_extension(tmp_path):
    async def go():
        class Provider:
            async def token(self,mint):
                return dict(symbol='USDC' if mint==USDC else 'OTC',decimals=6)
        token=SEEDS['OTC']
        class Client:
            markets={'OTC/USDT:USDT':dict(symbol='OTC/USDT:USDT',base='OTC',quote='USDT',settle='USDT',swap=True,linear=True,active=True,contractSize=10)}
        def engine(venue):
            return SimpleNamespace(clock=lambda:100, state={'experiment_config':{'venue':venue}},source=SimpleNamespace(routes=[],public={venue:Client()},provider=SimpleNamespace(solana=Provider())))
        engines={'dex_channel':engine('mexc'),'dex_bybit':engine('bybit')}
        d=Discovery(engines,tmp_path/'routes.json')
        await d.signal(f'SHORT? #OTC\n#SOLANA\n{token}',99)
        assert len(engines['dex_channel'].source.routes)==len(engines['dex_bybit'].source.routes)==1
        assert engines['dex_channel'].source.routes[0]['asset']==token
        assert engines['dex_bybit'].source.routes[0]['cex_venue']=='bybit'
        assert engines['dex_bybit'].source.routes[0]['notional_usd']==100
        await d.ensure('mexc','OTC',token)
        assert len(engines['dex_channel'].source.routes)==1
        with pytest.raises(ValueError):await d.signal(f'SHORT? #OTC\n#SOLANA\n{token}',1)
        with pytest.raises(ValueError):await d.ensure('mexc','FAKE',token)
        assert d.path.exists()
    asyncio.run(go())


def test_discovery_restart_restores_cached_routes_with_each_portfolio_size(tmp_path):
    import json
    from test_dex_virtual_paper import ROUTE
    async def go():
        engines={name:SimpleNamespace(state={"experiment_config":{"venue":venue,"notional":size}},
                   source=SimpleNamespace(routes=[]))
                 for name,venue,size in (("dex_channel","mexc",100),("dex_bybit","bybit",80))}
        path=tmp_path/"routes.json"
        cached={name:[dict(ROUTE,cex_venue=e.state["experiment_config"]["venue"],mode="reference",notional_usd=20,max_notional_usd=20)]
                for name,e in engines.items()}
        path.write_text(json.dumps(cached))
        d=Discovery(engines,path)
        async def idle():
            await asyncio.Event().wait()
        d.run=idle
        await d.start()
        assert engines['dex_channel'].source.routes[0]['notional_usd']==100
        assert engines['dex_bybit'].source.routes[0]['notional_usd']==80
        await d.stop()
        await d.start()
        assert all(len(e.source.routes)==1 for e in engines.values())
        await d.stop()
    asyncio.run(go())
