"""Regression checks use synthetic fixtures, never claim market executions."""
import asyncio
import copy
from decimal import Decimal as D
from types import SimpleNamespace as NS

import pytest

from app.evidence_error import EvidenceError
from app.preflight_progress import current, snapshot
from app.secret_redaction import redact
from app.dex_transfer_source import Source
from test_dex_transfer_paper import setup, next_stage
from test_dex_tax_evidence import payload, parse, POOL, STABLE, ASSET
from test_dex_outage_regressions import Session, Response, paired_source_with_rate
from app.kyber_research import Provider


def test_simulation_counter_asset_must_match_requested_pair():
    raw=payload(withToken={'address':'0x'+'9'*40})
    with pytest.raises(ValueError,match='COUNTER_ASSET_MISMATCH'):
        parse(raw,1,STABLE,POOL,ASSET,100)


def test_honeypot_in_pool_remains_blocked_and_is_not_global_asset_verdict():
    from app.dex_tax_evidence import Reader
    async def go():
        reader=Reader(Session(Response(200,payload(honeypotResult={'isHoneypot':True}))),lambda:100)
        reader.budget.spacing=0
        with pytest.raises(EvidenceError,match='HONEYPOT_DETECTED') as caught:
            await reader.token(1,STABLE,POOL,ASSET)
        d=caught.value.diagnostics
        assert d['pool']==POOL and d['contract']==STABLE
        assert d['global_asset_verdict'] is False and d['evidence_scope']=='TOKEN_IN_SPECIFIC_POOL'
    asyncio.run(go())


def test_counter_redaction_keeps_counts_without_exposing_secret_strings():
    result=redact({'DEX_VIRTUAL_TOKEN_TAX_UNSUPPORTED':3,'BOT_TOKEN':'secret',
                   'DEX_API_TOKEN':'secret','DEX_TOKEN_VALUE':False})
    assert result=={'DEX_VIRTUAL_TOKEN_TAX_UNSUPPORTED':3,'BOT_TOKEN':'***',
                    'DEX_API_TOKEN':'***','DEX_TOKEN_VALUE':'***'}


def test_kyber_message_classification_never_persists_free_text():
    async def go():
        p=Provider(NS(),Session(Response(400,{'code':4008,'message':'Route not found'})),clock=lambda:100)
        result=await p.price(1,STABLE,ASSET,'100')
        assert result['response_evidence']['message_category']=='ROUTE_NOT_FOUND'
        assert len(result['response_evidence']['message_sha256'])==64
        p=Provider(NS(),Session(Response(400,{'code':4008,'message':'private secret bearer xyz'})),clock=lambda:100)
        result=await p.price(1,STABLE,ASSET,'100')
        assert result['response_evidence']['message_category']=='UNCLASSIFIED'
        assert 'bearer' not in str(result)
    asyncio.run(go())


def test_recovery_reader_retries_old_book_but_never_accepts_stale_data():
    from test_dex_virtual_paper import Public
    from app.recovery_market import Reader
    from app.exchange_executor import SubmitRequest
    async def go():
        c=Public(lambda:100);calls=[]
        original=c.fetch_order_book
        async def book(*args,**kw):
            result=await original(*args,**kw);calls.append(1)
            if len(calls)==1:result['timestamp']=98000
            return result
        c.fetch_order_book=book
        req=SubmitRequest('X/USDT:USDT','sell',1,'market')
        x=await Reader({'bybit':c},clock=lambda:100).quote('bybit',req,1)
        assert len(calls)==2 and x.market_evidence['book_ts']==100
    asyncio.run(go())


def test_native_cex_cap_suggests_smaller_dex_purchase():
    async def go():
        s,r,c,_,_,_=paired_source_with_rate()
        r.update(notional_usd=100,max_notional_usd=100)
        c.bid=1.06
        result=await s.entry(r)
        attempts=result.get('position',{}).get('native_size_attempts',result.get('diagnostics',{}).get('native_size_attempts',[]))
        assert len(attempts)>1
        assert attempts[-1]['notional_usd']<attempts[0]['notional_usd']
        assert result.get('reason')!='DEX_VIRTUAL_NOTIONAL_OR_RESIDUAL_LIMIT',result
        if result.get('ok'):
            assert result['position']['base_qty']*result['position']['entry_price']<=100
    asyncio.run(go())


def test_network_rejection_preserves_which_operation_is_unavailable(tmp_path):
    async def go():
        feed,s,c,_,_=await setup(tmp_path)
        c.currencies['X']['networks']['BSC']['deposit']=False
        with pytest.raises(EvidenceError) as caught:
            await s.networks(s.catalog()[0],{'X':{'deposit'},'USDT':set()})
        assert caught.value.diagnostics['required_operations']==['deposit']
        assert caught.value.diagnostics['operation_status']=={'deposit':False}
    asyncio.run(go())


@pytest.mark.parametrize('direction',[0,1])
def test_eth_weth_paper_conversion_is_explicit_charged_and_persisted(tmp_path,direction):
    async def go():
        feed,s,c,_,now=await setup(tmp_path)
        weth='0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2'
        usdt='0xdac17f958d2ee523a2206206994597c13d831ec7'
        route=dict(s.catalog()[0],base='ETH',asset=weth,asset_decimals=18,chain_id=1,
                   stable=usdt,stable_decimals=6,spot_symbol='ETH/USDT',native_spot='ETH/USDT')
        c.markets['ETH/USDT']=dict(c.markets['X/USDT'],symbol='ETH/USDT',base='ETH')
        def net(contract):
            return dict(network='ETH',deposit=True,withdraw=True,fee='.0001',precision='.000001',
                        limits={'withdraw':{'min':'.0002'},'deposit':{'min':'.0001'}},
                        info={'chain':'ETH','contractAddress':contract,'withdrawPercentageFee':'0'})
        c.currencies['ETH']=dict(code='ETH',networks={'ETH':net('')})
        c.currencies['USDT']=dict(code='USDT',networks={'ETH':net(usdt)})
        class Prices:
            async def price(self,chain,sell,buy,amount):
                qty=D(amount)/D(10)**(18 if sell==weth else 6)
                price=D('1.08') if direction==0 else D(1)
                out=qty*price if sell==weth else qty/price
                return dict(ok=True,chain_id=chain,sell_token=sell,buy_token=buy,sell_amount_raw=amount,
                    buy_amount_raw=str(int(out*D(10)**(18 if buy==weth else 6))),ts=now[0],
                    gas_price_raw='1000000',network_fee_raw='100000000000',network_fee_usd_extra='0',
                    token_metadata={side:{f:'0' for f in ('buyTaxBps','sellTaxBps','transferTaxBps')} for side in ('sellToken','buyToken')})
        s.quote.provider=Prices()
        if direction==0:c.bid=D('.999');c.ask=D(1)
        await feed.store.set_control('budget',100)
        s.catalog=lambda:[route]
        buy,sell=('bybit','dex1') if direction==0 else ('dex1','bybit')
        e=await s.review('ETH/USDT',buy,sell,route=route)
        assert not e['reasons'],e
        t=e['transfer']['preview_base'];conversion=t['conversion']
        assert conversion['operation']==('WRAP_ETH_TO_WETH' if direction==0 else 'UNWRAP_WETH_TO_ETH')
        assert conversion['gas_units']==30000 and not conversion['execution_verified']
        assert D(t['gas'])>0
        await feed.paper.open(dict(signal=e,transfer=e['transfer']))
        for _ in range(12):
            p=feed.paper.state.get('position')
            if not p:break
            await next_stage(feed,now)
        assert feed.paper.state['closed']==1
        final=feed.paper.state['last_position']
        assert final['base_transfer']['conversion']['operation']==conversion['operation']
        assert D(final['gas_used'])>0
    asyncio.run(go())
