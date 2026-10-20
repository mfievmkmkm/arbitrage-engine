"""Controlled test quotes, never market fills or strategy performance evidence."""
import asyncio
import copy
import json
import sqlite3
from types import SimpleNamespace as NS

import pytest

from app.dex_tax_evidence import Reader
from app.kyber_research import Provider
from app.evidence_error import EvidenceError
from app.payload_codec import loads
from app.transfer_signal_math import limits
from test_dex_tax_evidence import payload, quote, POOL, STABLE, ASSET
from test_dex_outage_regressions import Session, Response
from test_kyber_research import response
from test_dex_transfer_paper import setup, R

MID='0x'+'4'*40
POOL2='0x'+'5'*40


def multihop():
    q=quote()
    q['route']=[[dict(exchange='uniswapv3',pool=POOL,tokenIn=STABLE,tokenOut=MID),
                 dict(exchange='uniswap',pool=POOL2,tokenIn=MID,tokenOut=ASSET)]]
    return q


class HopHTTP:
    def __init__(self,fail=None):self.calls=[];self.fail=fail
    def get(self,url,params,**kwargs):
        self.calls.append(dict(params))
        token,pool=params['address'],params['pair']
        a,b=(STABLE,MID) if pool==POOL else (MID,ASSET)
        raw=payload(token,b if token==a else a)
        raw['pairAddress']=pool;raw['pair']['pair']['address']=pool
        if (token,pool)==self.fail:raw['simulationResult']['transferTax']=1
        return Response(200,raw)


def test_composed_route_checks_intermediate_in_both_actual_pools():
    async def go():
        http=HopHTTP();reader=Reader(http,lambda:100);reader.budget.spacing=0
        metadata=await reader.route(multihop())
        assert {(x['address'],x['pair']) for x in http.calls}=={
            (STABLE,POOL),(MID,POOL),(MID,POOL2),(ASSET,POOL2)}
        assert metadata['route_evidence']['all_hops_zero_tax'] is True
        assert not metadata['route_evidence']['execution_verified']
        await reader.route(multihop())
        assert len(http.calls)==4
    asyncio.run(go())


def test_intermediate_tax_cannot_hide_behind_zero_endpoint_taxes():
    async def go():
        reader=Reader(HopHTTP((MID,POOL2)),lambda:100);reader.budget.spacing=0
        with pytest.raises(EvidenceError,match='TOKEN_TAX_UNSUPPORTED') as caught:
            await reader.route(multihop())
        assert caught.value.diagnostics['contract']==MID
    asyncio.run(go())


@pytest.mark.parametrize('change',['disconnected','cycle','unverified_protocol','wrong_end'])
def test_invalid_multihop_rejected_before_any_simulation_request(change):
    async def go():
        q=multihop()
        if change=='disconnected':q['route'][0][1]['tokenIn']=STABLE
        if change=='cycle':q['route'][0][1]['tokenOut']=STABLE
        if change=='unverified_protocol':q['route'][0][1]['exchange']='uniswap-v4'
        if change=='wrong_end':q['route'][0][1]['tokenOut']='0x'+'6'*40
        http=HopHTTP();r=Reader(http,lambda:100)
        with pytest.raises(ValueError,match='ROUTE_UNSUPPORTED'):await r.route(q)
        assert not http.calls
    asyncio.run(go())


def test_error_scope_survives_negative_cache_and_provider_to_strategy():
    from app.dex_virtual_paper_source import Source
    async def go():
        http=Session(Response(404));reader=Reader(http,lambda:100);reader.budget.spacing=0
        for _ in range(2):
            with pytest.raises(EvidenceError) as e:await reader.token(1,STABLE,POOL,ASSET)
            assert e.value.diagnostics['http_status']==404
            assert e.value.diagnostics['pool']==POOL
        assert http.calls==1
        q=quote()|dict(ok=True,sell_amount_raw='100',buy_amount_raw='200',ts=100,
                      tax_reason='DEX_TAX_HTTP_404',tax_diagnostics=e.value.diagnostics)
        source=Source.__new__(Source);source.clock=lambda:100
        source.provider=NS(price_route=lambda *a:asyncio.sleep(0,result=q))
        with pytest.raises(EvidenceError) as caught:
            await source.price(dict(chain_id=1,stable=STABLE,asset=ASSET,mode='paired'),False,100)
        assert caught.value.diagnostics['http_status']==404
    asyncio.run(go())


def test_failed_simulation_is_not_reported_as_detected_honeypot():
    from app.dex_tax_evidence import parse
    raw=payload(simulationSuccess=False,simulationError='Insufficient liquidity; SECRET_TEXT')
    with pytest.raises(EvidenceError,match='SIMULATION_FAILED') as caught:
        parse(raw,1,STABLE,POOL,ASSET,100)
    assert caught.value.diagnostics['simulation_error_category']=='INSUFFICIENT_LIQUIDITY'
    assert 'SECRET_TEXT' not in str(caught.value.diagnostics)


def test_cold_simulation_requotes_and_revalidates_changed_path():
    async def go():
        now=[100];calls=[]
        class Taxes:
            async def route(self,q):
                calls.append(q['route'])
                if len(calls)==1:now[0]+=4
                elif q['route']!=calls[0]:raise EvidenceError('DEX_TAX_ROUTE_UNSUPPORTED',pool='changed')
                return {}
        http=Session(Response(200,response()),Response(200,response(timestamp=104,route=[[{'exchange':'changed'}]])))
        p=Provider(NS(),http,clock=lambda:now[0],tax_reader=Taxes());p.budget.spacing=0
        q=await p.price(1,STABLE,ASSET,'100')
        assert http.calls==2 and q['ts']==104
        assert q['tax_reason']=='DEX_TAX_ROUTE_UNSUPPORTED'
        assert len(calls)==2
    asyncio.run(go())


def test_transfer_failure_has_independent_cost_and_stress_numbers(tmp_path):
    async def go():
        feed,s,c,prices,now=await setup(tmp_path)
        await feed.store.set_control('budget',100)
        await feed.store.set_control('min_spread',2)
        prices.token_price=__import__('decimal').Decimal('1.05')
        e=await s.review('X/USDT','bybit','dex56')
        # This controlled quote fails range/NET; it still needs an auditable breakdown.
        assert e['reasons']
        economics=e['transfer']['economics']
        assert economics['risk_evidence']=='OPERATOR_PAPER_SCENARIO_NOT_A_FEE'
        assert float(economics['stress_net'])<float(economics['net'])
        with sqlite3.connect(feed.journal.path) as db:
            failures=db.execute('SELECT payload FROM arb_checks WHERE candidate_id=? AND status="FAIL"',(e['trace_id'],)).fetchall()
        assert failures and all(loads(x[0]).get('economics') for x in failures)
        assert not feed.paper.state['position']
    asyncio.run(go())


def test_native_market_limit_failure_reports_value_and_minimum():
    with pytest.raises(EvidenceError) as caught:
        limits(dict(symbol='X/USDT',limits={'amount':{'min':1},'cost':{'min':5}}),2,4)
    assert caught.value.diagnostics==dict(observed='4',minimum='5',maximum=None,unit='cost',symbol='X/USDT')


def test_refresh_trigger_does_not_silently_tighten_existing_quote_age_limit():
    async def go():
        now=[100]
        class Taxes:
            async def route(self,q):now[0]+=3;return {}
        p=Provider(NS(),Session(Response(200,response()),Response(200,response(timestamp=103))),
                   clock=lambda:now[0],tax_reader=Taxes())
        p.budget.spacing=0
        q=await p.price(1,STABLE,ASSET,'100')
        assert q['ts']==103 and now[0]==106
        assert 'tax_reason' not in q and q['ok']
    asyncio.run(go())
