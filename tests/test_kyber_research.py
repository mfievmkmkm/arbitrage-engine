import asyncio
from types import SimpleNamespace as NS
import pytest
from app.kyber_research import Provider
from app.dex_paired_experiment import PairedSource
from app.dex_virtual_paper_source import Source
from test_dex_outage_regressions import Session, Response
from test_dex_virtual_paper import STABLE, ASSET


def response(**changes):
    q=dict(tokenIn=STABLE,tokenOut=ASSET,amountIn='100',amountOut='200',
           gas='200000',gasPrice='1000000000',l1FeeUsd='0.03',
           extraFee={'feeAmount':''},timestamp=100,route=[[{'exchange':'uniswap-v3'}]])
    q.update(changes)
    return dict(code=0,data=dict(routeSummary=q))


def test_get_quote_preserves_costs_and_unknown_taxes():
    async def go():
        session=Session(Response(200,response()))
        p=Provider(NS(),session,clock=lambda:100)
        q=await p.price(1,STABLE,ASSET,'100')
        assert q['ok'] and q['network_fee_raw']=='200000000000000'
        assert q['network_fee_usd_extra']=='0.03'
        assert q['token_metadata'] is None and not q['live_allowed']
        assert p.health()['responses_ok']==1 and session.calls==1
        # Receiving a price cannot bypass the strategy's tax evidence gate.
        router=NS(price_route=lambda *args: asyncio.sleep(0,result=q))
        s=Source.__new__(Source);s.provider=router;s.clock=lambda:100
        with pytest.raises(ValueError,match='DEX_VIRTUAL_TAX'):
            await s.price(dict(chain_id=1,stable=STABLE,asset=ASSET,mode='paired'),False,100)
    asyncio.run(go())


@pytest.mark.parametrize('changes,reason',[
    ({'tokenOut':STABLE},'KYBER_RESPONSE_SCOPE_MISMATCH'),
    ({'amountIn':'101'},'KYBER_RESPONSE_SCOPE_MISMATCH'),
    ({'amountOut':'NaN'},'KYBER_AMOUNT_INVALID'),
    ({'timestamp':80},'KYBER_ROUTE_STALE'),
    ({'timestamp':103},'KYBER_ROUTE_STALE'),
    ({'gasPrice':None},'KYBER_COST_UNKNOWN'),
    ({'l1FeeUsd':None},'KYBER_COST_UNKNOWN'),
    ({'gas':'NaN'},'KYBER_COST_INVALID'),
    ({'extraFee':{'feeAmount':'1'}},'KYBER_EXTRA_FEE_UNSUPPORTED'),
    ({'route':[]},'KYBER_ROUTE_UNAVAILABLE'),
])
def test_unusable_evidence_is_rejected(changes,reason):
    async def go():
        p=Provider(NS(),Session(Response(200,response(**changes))),clock=lambda:100)
        assert (await p.price(1,STABLE,ASSET,'100'))['reason']==reason
        assert p.responses_ok==0
    asyncio.run(go())


def test_global_throttle_stops_other_chains_and_preserves_retry_after():
    async def go():
        session=Session(Response(429,headers={'Retry-After':'120'}))
        p=Provider(NS(),session,clock=lambda:100)
        p.budget.spacing=0
        assert (await p.price(1,STABLE,ASSET,'100'))['retry_after_seconds']==120
        assert (await p.price(56,STABLE,ASSET,'100'))['request_sent'] is False
        assert session.calls==1 and p.health()['blocked']
    asyncio.run(go())


def test_auth_failures_back_off_without_new_account_or_fallback():
    async def go():
        session=Session(Response(403))
        p=Provider(NS(),session,clock=lambda:100)
        first=await p.price(1,STABLE,ASSET,'100')
        assert first['reason']=='DEX_HTTP_403'
        assert (await p.price(1,STABLE,ASSET,'100'))['request_sent'] is False
        assert session.calls==1
    asyncio.run(go())


def test_unsupported_chain_does_not_send_http():
    async def go():
        session=Session();p=Provider(NS(),session,clock=lambda:100)
        assert (await p.price(999,STABLE,ASSET,'100'))['reason']=='KYBER_CHAIN_UNSUPPORTED'
        assert not session.calls
    asyncio.run(go())


def test_monitor_uses_persisted_provider_without_mutating_scanner(monkeypatch):
    seen=[]
    async def exit(self,p):
        seen.append(self.provider)
        return dict(ok=True)
    monkeypatch.setattr(Source,'exit',exit)
    async def go():
        s=PairedSource.__new__(PairedSource)
        old,new=object(),object()
        s.provider=new;s.price_providers={'0x':old,'KyberSwap':new}
        await s.exit({'entry_dex':{'provider':'0x'}})
        await s.exit({'paired_quote_provider':'KyberSwap'})
        assert seen==[old,new] and s.provider is new
        assert (await s.exit({'paired_quote_provider':'unknown'}))['reason']=='PAIRED_POSITION_PROVIDER_UNAVAILABLE'
    asyncio.run(go())


def test_l1_cost_is_added_and_can_exceed_risk_cap():
    from test_dex_virtual_paper import source
    async def go():
        s,r,*_=source()
        q=dict(network_fee_raw='1000000000000',network_fee_usd_extra='.03')
        cost,_,_=await s.gas(r,q)
        assert .0309<cost<.0311
        with pytest.raises(ValueError,match='GAS_BUDGET_LIMIT'):
            await s.gas(r,dict(q,network_fee_usd_extra='.25'))
        with pytest.raises(ValueError,match='EXTRA_GAS_UNKNOWN'):
            await s.gas(r,dict(q,network_fee_usd_extra=None))
    asyncio.run(go())


def test_console_separates_successful_quotes_from_trade_admission():
    from app.paired_console import scanner
    scan=NS(last_batch_seconds=.5,last_batch_at=100,last_sweep_seconds=4,
            catalog={1:{}},workers=3,checked=3,last_reasons={'DEX_VIRTUAL_TAX_INVALID':3},
            provider_health=dict(provider='KyberSwap',requests_sent=3,responses_ok=3))
    text,_=scanner(NS(clock=lambda:100),NS(scanner=scan,candidates={}))
    assert 'KyberSwap с запуска' in text and 'Комиссии токенов не подтверждены' in text
    assert 'Подходящих проверенных пар сейчас нет' not in text
