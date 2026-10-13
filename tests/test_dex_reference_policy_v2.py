import asyncio
from types import SimpleNamespace
import pytest
from app.dex_reference_policy import risk_plan, exit_reason, entry_quality, record_close, blocked_until
from app.dex_cex_identity import matching_contract


def test_significant_profit_only_and_target_not_proportional_to_extreme_anchor():
    p=dict(reference_policy=risk_plan(100, 12), opened_at=0, best_net=.0072)
    def mark(net,ts=12):return dict(ts=ts,gross=net+.2,entry_fees=.05,exit_fees=.05,safety=.1,net=net)
    assert exit_reason(p,mark(-.1),True) is None
    assert exit_reason(p,mark(-.8),True)=='NET_STOP'
    assert exit_reason(p,mark(.76),True)=='NET_CAPTURE'
    p['best_net']=.5
    assert exit_reason(p,mark(.34),True)=='PROFIT_GIVEBACK'
    assert entry_quality(100,12,-.2)=='DEX_V2_REFERENCE_GAP_EXTREME'
    assert entry_quality(100,1,-.2)=='DEX_V2_REWARD_RISK_LOW'


def test_unknown_funding_does_not_block_risk_or_fake_profit():
    p=dict(reference_policy=risk_plan(100,3),opened_at=0,best_net=1)
    m=dict(ts=10,gross=-1,entry_fees=.05,exit_fees=.05,safety=.1,net=-1.2)
    assert exit_reason(p,m,False)=='NET_STOP'
    m.update(gross=1,net=.8)
    assert exit_reason(p,m,False) is None
    m['ts']=600
    assert exit_reason(p,m,False)=='TIME_STOP'


def test_symbol_loss_pause_across_chains_and_restart():
    import json
    state={}
    for ts in (100,4000):record_close(state,dict(symbol='DOS/USDT:USDT',net=-1,closed_at=ts))
    restored=json.loads(json.dumps(state))
    assert blocked_until(restored,dict(base='DOS',chain_id=1))==86400
    assert blocked_until(restored,dict(base='DOS',chain_id=56))==86400
    assert blocked_until(restored,dict(base='OTHER'))==0


def test_exchange_identity_requires_chain_and_exact_address():
    route=dict(base='X',chain_id=1,asset='0x'+'2'*40)
    currency=dict(code='X',networks={'ERC20':dict(info={'contractAddress':route['asset']})})
    assert matching_contract(currency,route)
    assert not matching_contract(dict(code='X'),route)
    assert not matching_contract(currency,dict(route,chain_id=56))
    assert not matching_contract(currency,dict(route,asset='0x'+'3'*40))
    sol=dict(base='SOL',chain_id='solana',asset='So11111111111111111111111111111111111111112')
    assert matching_contract(dict(code='SOL',networks={'SOL':dict(contract='')}),sol)
    assert not matching_contract(dict(code='SOL',networks={'SOL':dict(contract='')}),dict(sol,asset='MukLDtJ8Cx9DxLbeyLRSWPSposTMWuwHANbuaudpump'))


def test_v2_entry_convergence_and_fast_exit_without_dex_io(tmp_path):
    from test_dex_virtual_paper import source
    from app.cex_dex_paper import Engine, Cycle
    from app.dex_cex_identity import Gate
    async def go():
        s,r,c,provider,now=source('reference')
        original_market = c.market
        c.market = lambda symbol: dict(original_market(symbol), taker=.00055)
        r.update(notional_usd=20,max_notional_usd=20)
        s.policy_version=2;s.fast_exit=True;s.focus=True
        c.currencies={'X':dict(code='X',networks={'ERC20':dict(contract=r['asset'])})}
        s.identity_gate=Gate(s.public,{},s.clock)
        e=Engine(tmp_path/'db',s,virtual=True,namespace='bybit',clock=s.clock)
        await e.init()
        cycle=Cycle(e,[r])
        await cycle.cycle();assert e.state['last_decision']=='DEX_REFERENCE_CONFIRMATION_REQUIRED'
        now[0]+=5
        await cycle.cycle();assert e.state['last_decision']=='DEX_V2_CONVERGENCE_UNCONFIRMED'
        now[0]+=5;c.bid=1.037
        await cycle.cycle();assert e.positions,e.state['last_decision']
        p=next(iter(e.positions.values()))
        assert p['reference_policy']['version']==2 and p['cex_identity']['base']=='X'
        assert cycle.interval==1
        before=len(provider.calls);c.bid=1;now[0]+=1
        closed=await e.cycle(None,False)
        assert closed and closed[0]['exit_reason']=='NET_CAPTURE'
        assert len(provider.calls)==before
        assert cycle.interval==5
    asyncio.run(go())


def test_policy_upgrade_imports_losses_once_and_preserves_old_budget(tmp_path):
    import sqlite3
    import json
    from test_dex_virtual_paper import source
    from app.cex_dex_paper import Engine
    from app.dex_paper_experiments import build
    from app.secondary_strategy_runtime import SecondaryRuntime
    from app.strategy_runtime import StrategyRuntime
    async def go():
        s,r,c,provider,now=source('reference')
        s.routes.append(dict(r,cex_venue='mexc'));s.public['mexc']=c
        db=tmp_path/'upgrade.db'
        original=Engine(db,s,virtual=True,clock=s.clock);await original.init()
        runtime=StrategyRuntime()
        bundle=SimpleNamespace(dex_paper=original,runtime=SecondaryRuntime(runtime,None,db))
        async def notify(*args):pass
        old=await build(bundle,runtime,str(db),str(tmp_path/'state'),notify,lambda p:True,strategy_version=1)
        with sqlite3.connect(db) as conn:
            for i in (1,2):
                p=dict(id=i,symbol='X/USDT:USDT',closed_at=90+i,net=-1)
                conn.execute('INSERT INTO cex_dex_virtual_paper_bybit VALUES(?,?,?,?,?,?,?)',
                             (i,p['symbol'],80,p['closed_at'],'CLOSED',-1,json.dumps(p)))
        for _ in range(2):
            newer=await build(bundle,runtime,str(db),str(tmp_path/'state'),notify,lambda p:True)
            e=newer['dex_bybit']
            assert e.state['policy_version']==2
            assert e.state['asset_risk']['X']['losses']==2
            assert e.source.blocked_until(r)==86400
            assert e.budget()==old['dex_bybit'].budget()
        with sqlite3.connect(db) as conn:
            assert conn.execute('SELECT COUNT(*) FROM cex_dex_virtual_paper_bybit').fetchone()[0]==2
    asyncio.run(go())


def test_paper_risk_quote_does_not_relax_live_evidence_validation():
    from test_dex_virtual_paper import Public
    from app.recovery_market import Reader, validate_evidence
    from app.exchange_executor import SubmitRequest
    async def go():
        c=Public(lambda:100)
        async def book(symbol,limit=20):
            return dict(symbol=symbol,timestamp=100000,bids=[[1,1],[.99,20]],asks=[[1.001,20]])
        c.fetch_order_book=book
        request=SubmitRequest('X/USDT:USDT','sell',10,'market',reduce_only=True)
        with pytest.raises(ValueError,match='SLIPPAGE_TOO_HIGH'):
            await Reader({'bybit':c},clock=lambda:100).quote('bybit',request,1)
        quote=await Reader({'bybit':c},clock=lambda:100,max_slippage=.20).quote('bybit',request,1)
        with pytest.raises(ValueError,match='SLIPPAGE_TOO_HIGH'):
            validate_evidence(quote,'bybit',100)
    asyncio.run(go())


def test_v2_reduced_size_does_not_reset_after_floor_or_transient_failure():
    from test_dex_virtual_paper import source
    async def go():
        s,r,_,_,now=source('reference')
        s.adaptive=True;s.policy_version=2
        r.update(notional_usd=100,max_notional_usd=100)
        calls=[]
        async def attempt(route):
            calls.append(route['notional_usd'])
            return dict(ok=False,reason='RECOVERY_BOOK_SLIPPAGE_TOO_HIGH' if len(calls)<=4 else 'DEX_VIRTUAL_ENTRY_UNAVAILABLE')
        s._entry=attempt
        for _ in range(6):
            await s.entry(r);now[0]+=5
        assert calls==[100,50,25,12.5,12.5,12.5]
    asyncio.run(go())
