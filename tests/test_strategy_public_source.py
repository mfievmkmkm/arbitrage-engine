import asyncio
import copy
from types import SimpleNamespace
import pytest
from app.strategy_public_source import Source, Runtime
from app.strategy_lab import Lab
from app.strategy_math import D, Invalid
from app.strategy_lab_examples import snapshot
from app.strategy_models import evaluate, policy


class Client:
    precisionMode=4
    def __init__(self,kind='perp',price=100):
        self.price=price
        self.markets={'COIN/USDT':dict(symbol='COIN/USDT',base='COIN',quote='USDT',active=True,
            spot=kind=='spot',swap=kind=='perp',future=kind=='future',option=False,
            linear=True,settle='USDT',contract=kind!='spot',contractSize=10 if kind!='spot' else None,
            precision={'amount':'.01'},limits={'amount':{'min':'.01'},'cost':{'min':'1'}},taker='.0005')}
    async def fetch_order_book(self,symbol,limit):
        return dict(symbol=symbol,timestamp=1000000,bids=[[str(self.price),2]],asks=[[str(self.price+.01),2]])


class Funding:
    async def get(self,venue,symbol):
        return SimpleNamespace(next_ts=2000000,interval_hours=8,rate=.001)


def routes():
    s=snapshot()
    s['books']=[dict(id=b['id'],client=b['venue'],venue=b['venue'],symbol='COIN/USDT',
                     base=b['base'],quote=b['quote'],kind=b['kind'],asset_id=b['asset_id'],
                     quote_asset_id=b['quote_asset_id'],identity_verified=True,
                     fee_currency='quote',fee_currency_verified=True) for b in s['books']]
    return [s]


def test_public_adapter_uses_actual_depth_native_multiplier_and_funding_calendar():
    async def go():
        source=Source({'a':Client(price=100),'b':Client(price=102)},routes(),Funding(),clock=lambda:1000)
        row=await source.snapshot('perp_convergence:synthetic-perp_convergence',policy())
        assert D(row['books'][0]['bids'][0][1])==20
        assert row['books'][0]['multiplier']=='10'
        assert row['books'][0]['step']=='0.01'
        assert row['source_origin']=='public_capture'
        result=evaluate(row,now=1000)
        assert result['allowed'],result
        assert D(result['plan']['quantity'])==D('.9')
        assert len(row['funding_events'])==2
    asyncio.run(go())


@pytest.mark.parametrize('change,reason',[
    ('fee','PUBLIC_FEES_UNKNOWN'),('settle','PUBLIC_DERIVATIVE_MISMATCH'),
    ('min','PUBLIC_MINIMUMS_UNKNOWN'),('precision','PUBLIC_PRECISION_MODE_UNSUPPORTED'),
    ('active','PUBLIC_MARKET_INACTIVE_OR_UNKNOWN'),('identity','PUBLIC_MARKET_IDENTITY_MISMATCH')])
def test_public_adapter_never_invents_missing_metadata(change,reason):
    async def go():
        a=Client();m=a.markets['COIN/USDT']
        if change=='fee':m['taker']=None
        elif change=='settle':m['settle']='BTC'
        elif change=='min':m['limits']['cost']['min']=None
        elif change=='precision':a.precisionMode=3
        elif change=='active':m['active']=None
        elif change=='identity':m['base']='OTHER'
        source=Source({'a':a,'b':Client(price=102)},routes(),Funding(),clock=lambda:1000)
        with pytest.raises(Invalid,match=reason):
            await source.snapshot('perp_convergence:synthetic-perp_convergence',policy())
    asyncio.run(go())


def test_public_failure_is_durable_and_never_removes_positions(tmp_path):
    async def go():
        lab=Lab(tmp_path/'lab.db',clock=lambda:1000);await lab.init()
        source=Source({},routes(),Funding(),clock=lambda:1000)
        runtime=Runtime(lab,source)
        await runtime.cycle()
        assert runtime.errors
        assert lab.state['source_errors']
        report=await lab.export()
        assert report['events'][-1]['reason'].startswith('Invalid:PUBLIC_CLIENT_UNAVAILABLE')
        await runtime.stop()
    asyncio.run(go())


def test_public_and_historical_replay_accounts_cannot_be_mixed(tmp_path):
    async def go():
        lab=Lab(tmp_path/'lab.db',clock=lambda:1000);await lab.init()
        s=snapshot();s['source_origin']='public_capture'
        await lab.process(dict(id='p',type='snapshot',snapshot=s),now=1000)
        with pytest.raises(Invalid,match='SEPARATE_REPLAY'):
            await lab.process(dict(id='r',type='snapshot',snapshot=snapshot(ts=1001)),now=1001)
    asyncio.run(go())
