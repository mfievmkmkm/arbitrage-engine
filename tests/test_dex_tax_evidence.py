import asyncio
import copy
from types import SimpleNamespace as NS
import pytest
from app.dex_tax_evidence import Reader, parse
from app.kyber_research import Provider
from app.cex_dex_paper import Engine
from test_dex_outage_regressions import Session, Response, paired_source_with_rate
from test_dex_virtual_paper import STABLE, ASSET

POOL='0x'+'3'*40


def payload(token=STABLE,other=ASSET,**changes):
    raw=dict(token=dict(address=token),chain=dict(id='1'),pairAddress=POOL,
             pair=dict(chainId='1',pair=dict(address=POOL,token0=token,token1=other)),
             simulationSuccess=True,honeypotResult=dict(isHoneypot=False),
             summary=dict(riskLevel=1,flags=[]),flags=[],
             simulationResult=dict(buyTax=0,sellTax=0,transferTax=0))
    raw.update(changes)
    return raw


def quote():
    return dict(chain_id=1,sell_token=STABLE,buy_token=ASSET,route=[[
        dict(exchange='uniswapv3',pool=POOL,tokenIn=STABLE,tokenOut=ASSET)]])


@pytest.mark.parametrize('field,value',[(k,v) for k in ('buyTax','sellTax','transferTax')
                                      for v in (None,'',True,'NaN',-1,101)])
def test_missing_or_invalid_taxes_never_become_zero(field,value):
    raw=payload();raw['simulationResult'][field]=value
    with pytest.raises(ValueError,match='DEX_TAX_'):
        parse(raw,1,STABLE,POOL,ASSET,100)


@pytest.mark.parametrize('changes,reason',[
    ({'chain':dict(id='56')},'SCOPE_MISMATCH'),
    ({'token':dict(address=ASSET)},'SCOPE_MISMATCH'),
    ({'pairAddress':ASSET},'SCOPE_MISMATCH'),
    ({'simulationSuccess':False},'SIMULATION_FAILED'),
    ({'honeypotResult':{}},'HONEYPOT_OR_UNKNOWN'),
    ({'honeypotResult':dict(isHoneypot=True)},'HONEYPOT_OR_UNKNOWN'),
    ({'summary':dict(riskLevel=20)},'RISK_OR_UNKNOWN'),
    ({'summary':{}},'RISK_OR_UNKNOWN'),
    ({'flags':['TRANSFER_BLOCKED']},'FLAGS_UNSUPPORTED'),
    ({'simulationResult':dict(buyTax=0,sellTax=0,transferTax=0,maxSell={'tokenWei':'1'})},'SIZE_LIMIT_UNSUPPORTED')
])
def test_identity_and_simulation_checks(changes,reason):
    with pytest.raises(ValueError,match=reason):
        parse(payload(**changes),1,STABLE,POOL,ASSET,100)


def test_tax_units_are_percentages_not_fractions_or_bps():
    raw=payload();raw['simulationResult']['buyTax']=1.5
    assert parse(raw,1,STABLE,POOL,ASSET,100)['buyTaxBps']=='150.0'


def test_single_flight_cache_is_scoped_and_expires():
    async def go():
        now=[100];session=Session(Response(200,payload()),Response(200,payload()))
        r=Reader(session,lambda:now[0]);r.budget.spacing=0
        rows=await asyncio.gather(*(r.token(1,STABLE,POOL,ASSET) for _ in range(3)))
        assert session.calls==1 and rows[0]['source']=='Honeypot.is'
        rows[0]['buyTaxBps']='100'
        assert (await r.token(1,STABLE,POOL,ASSET))['buyTaxBps']=='0'
        now[0]=160
        await r.token(1,STABLE,POOL,ASSET)
        assert session.calls==2
        with pytest.raises(ValueError,match='CHAIN_UNSUPPORTED'):
            await r.token(42161,STABLE,POOL,ASSET)
        assert session.calls==2
    asyncio.run(go())


def test_limiter_does_not_retry_other_tokens_after_429():
    async def go():
        session=Session(Response(429,headers={'Retry-After':'120'}))
        r=Reader(session,lambda:100);r.budget.spacing=0
        for token,other in [(STABLE,ASSET),(ASSET,STABLE)]:
            with pytest.raises(ValueError,match='HTTP_429'):
                await r.token(1,token,POOL,other)
        assert session.calls==1 and r.blocked_until==220
    asyncio.run(go())


@pytest.mark.parametrize('kind',['hop','protocol','pool','tokens'])
def test_unknown_paths_do_not_reuse_unrelated_token_evidence(kind):
    async def go():
        r=Reader(Session(),lambda:100);q=quote()
        if kind=='hop':q['route'][0].append(copy.deepcopy(q['route'][0][0]))
        if kind=='protocol':q['route'][0][0]['exchange']='uniswap-v4'
        if kind=='pool':q['route'][0][0]['pool']='unknown'
        if kind=='tokens':q['route'][0][0]['tokenOut']=STABLE
        with pytest.raises(ValueError,match='ROUTE_UNSUPPORTED'):await r.route(q)
        assert r.requests_sent==0
    asyncio.run(go())


def test_paired_cycle_uses_real_adapter_shapes_and_preserves_both_legs(tmp_path):
    # Synthetic replay verifies the mechanism; never recorded as market profit.
    async def go():
        source,route,public,_,now,_=paired_source_with_rate()
        calls=[]
        class HTTP:
            def get(self,url,params,**kwargs):
                calls.append((url,dict(params)))
                if 'honeypot.is' in url:
                    token=params['address'];other=ASSET if token.lower()==STABLE.lower() else STABLE
                    return Response(200,payload(token,other))
                sell,buy=params['tokenIn'],params['tokenOut'];sold=int(params['amountIn'])
                bought=sold*10000 if sell==ASSET else sold//10000
                q=quote()['route'][0][0]|dict(tokenIn=sell,tokenOut=buy)
                return Response(200,dict(code=0,data=dict(routeSummary=dict(
                    tokenIn=sell,tokenOut=buy,amountIn=str(sold),amountOut=str(bought),
                    gas='10000',gasPrice='1000000',l1FeeUsd='0',extraFee={'feeAmount':''},
                    timestamp=now[0],route=[[q]]))))
        http=HTTP();tax=Reader(http,source.clock);tax.budget.spacing=0
        provider=Provider(NS(),http,clock=source.clock,tax_reader=tax);provider.budget.spacing=0
        source.provider=provider
        engine=Engine(tmp_path/'db',source,capital=250,virtual=True,namespace='paired',clock=source.clock)
        await engine.init();await engine.cycle(route)
        assert len(engine.positions)==1
        p=next(iter(engine.positions.values()))
        assert p['paired_quote_provider']=='KyberSwap'
        assert p['base_qty']==p['virtual_asset_qty'] and p['cex_side']=='sell'
        assert p['entry_dex']['token_metadata']['buyToken']['evidence'][0]['pool']==POOL
        assert p['live_allowed'] is False
        public.bid=1.01;now[0]+=1
        closed=await engine.cycle(entry_enabled=False)
        assert len(closed)==1 and not engine.positions and closed[0]['net']>0
        assert 'dex_pnl' in closed[0]['last_mark'] and 'cex_pnl' in closed[0]['last_mark']
        assert all('routes/build' not in url for url,_ in calls)
        kyber=[params for url,params in calls if 'kyberswap' in url]
        assert kyber and all(p['onlyDirectPools']=='true' and p['includedSources']=='uniswap,uniswapv3' for p in kyber)
    asyncio.run(go())


def test_compact_transfer_status_does_not_expose_adapter_inventory(tmp_path):
    from app.market_signals import Scanner
    s=Scanner(tmp_path/'db',None,clock=lambda:100,venues=[],trade_venues={'bybit'})
    s.clients={'bybit':NS()};s.health={'bybit':'OK'};s.tickers={'bybit':(99,{})};s.last_scan=99
    text=s.status()
    assert 'bybit' in text and 'две подключённые spot-биржи' in text
    assert 'CCXT' not in text and 'адаптеров' not in text and len(text)<300


def test_nonzero_observed_tax_still_blocks_paired_trade():
    from app.dex_virtual_paper_source import Source
    async def go():
        raw=payload();raw['simulationResult']['transferTax']=.1
        reader=Reader(Session(Response(200,raw),Response(200,payload(ASSET,STABLE))),lambda:100)
        reader.budget.spacing=0
        metadata=await reader.route(quote())
        q=quote()|dict(ok=True,sell_amount_raw='100',buy_amount_raw='200',ts=100,token_metadata=metadata)
        source=Source.__new__(Source);source.clock=lambda:100
        source.provider=NS(price_route=lambda *args:asyncio.sleep(0,result=q))
        with pytest.raises(ValueError,match='TOKEN_TAX_UNSUPPORTED'):
            await source.price(dict(chain_id=1,stable=STABLE,asset=ASSET,mode='paired'),False,100)
    asyncio.run(go())


def test_expired_observation_cannot_be_refreshed_by_cache_read():
    async def go():
        clock=[100];r=Reader(Session(Response(200,payload()),Response(503)),lambda:clock[0]);r.budget.spacing=0
        first=await r.token(1,STABLE,POOL,ASSET)
        clock[0]=161
        with pytest.raises(ValueError,match='HTTP_503'):await r.token(1,STABLE,POOL,ASSET)
        assert first['observed_at']==100
    asyncio.run(go())


def test_unsupported_tax_chain_does_not_spend_quote_requests():
    async def go():
        http=Session();reader=Reader(http,lambda:100)
        provider=Provider(NS(),http,clock=lambda:100,tax_reader=reader)
        q=await provider.price(42161,STABLE,ASSET,'100')
        assert not q['ok'] and q['reason']=='DEX_TAX_CHAIN_UNSUPPORTED'
        assert http.calls==0 and provider.requests_sent==0
    asyncio.run(go())
