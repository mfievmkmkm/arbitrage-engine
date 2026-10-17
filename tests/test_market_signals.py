import asyncio
from types import SimpleNamespace as NS
import pytest
from app.market_signals import Scanner, economics, discover
from app.market_signal_store import Store
from app.channel_signal_review import Reviewer
from app.market_signal_links import market_link
from app.dex_public_channel_reader import Reader
from app.strategy_math import D


def book(price,quantity=10):
    return dict(asks=[[str(price),str(quantity)]],bids=[[str(price-1),str(quantity)]],
                step=D('.001'),fee_rate=D('.002'),ts=D(100))


def test_equal_base_fees_and_total_capital():
    e=economics(book(100),book(107),25,250)
    q=D(e['qty'])
    assert q==D('.249')
    assert D(e['spread_pct'])==6
    assert D(e['trade_net'])==q*6-q*206*D('.002')
    assert D(e['capital_required'])==q*(D('100.2')+106)
    assert D(e['buy_cash'])+q*100*D('.002')<=25
    with pytest.raises(ValueError,match='депозита'):
        economics(book(100),book(107),25,30)
    with pytest.raises(ValueError,match='глубина'):
        economics(book(100,quantity=.1),book(107))


def test_discovery_excludes_dex_and_includes_supported_spot_adapters():
    venues=discover()
    assert {'gate','mexc','bybit','binance','okx'}<=set(venues)
    assert not {'hyperliquid','lighter','aster','pacifica','nado'}&set(venues)
    assert len(venues)==len(set(venues))


def test_bounded_exchange_pool_rotates_without_evicting_configured_venue(tmp_path,monkeypatch):
    import app.market_signals as signals
    async def go():
        closed=[]
        class Client:
            def __init__(self,name):
                self.name=name;self.markets={'X/USDT':dict(spot=True,active=True,quote='USDT',base='X')}
            async def load_markets(self):pass
            async def close(self):closed.append(self.name)
        for name in ('bybit','gate','mexc','okx'):
            monkeypatch.setattr(signals.ccxt,name,lambda opts,name=name:Client(name))
        shared=Client('bybit')
        scanner=Scanner(tmp_path/'db',None,venues=['bybit','gate','mexc','okx'],trade_venues={'bybit'},shared_clients={'bybit':shared})
        async def idle(*args):await asyncio.sleep(3600)
        scanner.ticker_loop=idle
        await scanner.init()
        for name in scanner.names:await scanner.load(name)
        assert len(scanner.clients)==2 and scanner.clients['bybit'] is shared
        assert closed==['gate','mexc']
        assert 'gate' not in scanner.tickers
        await scanner.close()
        assert 'bybit' not in closed and 'okx' in closed
    asyncio.run(go())


def test_durable_queue_dedup_toggle_and_delivery_retry(tmp_path):
    async def go():
        now=[100];sent=[]
        scanner=Scanner(tmp_path/'db',lambda text:None,clock=lambda:now[0],venues=[])
        await scanner.init()
        await scanner.queue_channel(dict(id=4,text='SHORT? #X',timestamp=100))
        await scanner.queue_channel(dict(id=4,text='changed',timestamp=100))
        assert len(await scanner.store.pending())==1
        reviews=[]
        async def review(post):reviews.append(post);return 'result'
        scanner.channel_review=review
        async def failing(text):raise RuntimeError('timeout')
        scanner.notify=failing
        with pytest.raises(RuntimeError):await scanner.flush()
        assert len(reviews)==1
        # Result survives restart; don't fetch changed prices and silently
        # replace the assessment after an ambiguous Telegram timeout.
        restored=Scanner(tmp_path/'db',failing,clock=lambda:now[0],venues=[])
        await restored.init()
        async def notify(text):sent.append(text)
        restored.notify=notify
        now[0]=200
        await restored.flush()
        assert len(sent)==1 and '\n\nresult\n\n🔎 CEX spot:' in sent[0] and 'Задержанная доставка' in sent[0]
        assert not await restored.store.pending()
        await restored.store.toggle()
        assert not await Store(tmp_path/'db').enabled()
        await restored.queue_channel(dict(id=5,text='LONG? #Y',timestamp=100))
        await restored.cycle()
        assert len(await restored.store.pending())==1
    asyncio.run(go())


def test_missing_identity_is_reported_not_qualified_and_links_present(tmp_path):
    async def go():
        s=Scanner(tmp_path/'db',None,clock=lambda:100,venues=[])
        await s.init()
        market=dict(taker=.001,limits=dict(amount=dict(min=.001),cost=dict(min=1)))
        s.clients={'bybit':NS(markets={'X/USDT':market},urls={'www':'https://www.bybit.com'}),
                   'gate':NS(markets={'X/USDT':market},urls={'www':'https://www.gate.com'})}
        async def route(*args):raise ValueError('Не подтверждён одинаковый токен: сеть и контракт')
        async def capture(venue,symbol):return book(100 if venue=='bybit' else 107)
        s.route=route;s.book=capture
        e=await s.evaluate('X/USDT','bybit','gate')
        assert e['transfer'] is None
        t=s.format(e)
        assert 'ВХОД НЕ ПОДТВЕРЖДЁН' in t and 'Невозможно проверить последовательный перевод' in t
        assert 'https://www.bybit.com/en/trade/spot/X/USDT' in t
        assert 'https://www.gate.com/trade/X_USDT' in t
        assert len(t)<4096
    asyncio.run(go())


def test_network_expense_turns_positive_spread_into_rejection(tmp_path):
    async def go():
        s=Scanner(tmp_path/'db',None,clock=lambda:100,venues=[])
        await s.init()
        m=dict(taker=.002,limits=dict(amount=dict(min=0),cost=dict(min=0)))
        s.clients={'a':NS(markets={'BTC/USDT':m}),'b':NS(markets={'BTC/USDT':m})}
        async def route(*args):return (dict(fee='.001',withdraw_minimum='.001',deposit_minimum='.001',deposit_fee='0',deposit_fee_percentage=False,network='BTC'),
                                       dict(fee='2',withdraw_minimum='1',deposit_minimum='1',deposit_fee='0',deposit_fee_percentage=False,network='TRC20'))
        async def capture(venue,symbol):return book(100 if venue=='a' else 107)
        s.route=route;s.book=capture
        e=await s.evaluate('BTC/USDT','a','b')
        assert D(e['transfer']['net_after_reserve'])<0 and any('NET после резерва' in r for r in e['reasons'])
        async def low(venue,symbol):return book(100 if venue=='a' else 105)
        s.book=low
        assert await s.evaluate('BTC/USDT','a','b') is None
    asyncio.run(go())


def test_stale_skew_and_extreme_gap_rejected(tmp_path):
    async def go():
        s=Scanner(tmp_path/'db',None,clock=lambda:100,venues=[])
        await s.init()
        async def route(*args):raise ValueError('unknown')
        async def capture(venue,symbol):return dict(book(100 if venue=='a' else 112),ts=D(98 if venue=='a' else 100))
        s.route=route;s.book=capture
        with pytest.raises(ValueError,match='синхронизированы'):
            await s.evaluate('X/USDT','a','b')
    asyncio.run(go())


def test_reader_reports_even_if_legacy_admission_impossible(tmp_path):
    async def go():
        store=Store(tmp_path/'db',clock=lambda:100);await store.init()
        engine=NS(path=str(tmp_path/'db'),clock=lambda:100)
        async def queue(post):await store.enqueue('channel:'+str(post['id']),'channel',post)
        reader=Reader(engine,lambda:True,on_post=queue,report_only=True)
        p=dict(id=1,text='SHORT? #UNKNOWN',timestamp=100)
        await reader.accept(p);await reader.accept(p)
        assert len(await store.pending())==1 and reader.metrics['received']==1
        reader2=Reader(engine,lambda:True,on_post=queue,report_only=True)
        await reader2.start()
        assert 1 in reader2.seen
        await reader2.stop()
        await reader2.accept(p)
        assert reader2.metrics['received']==1
    asyncio.run(go())


def test_channel_never_treats_outcome_as_entry_or_missing_inventory_as_arb():
    async def go():
        review=Reviewer(NS(),clock=lambda:100)
        stale=await review(dict(id=1,text='SHORT? #X',timestamp=1))
        assert 'старше 90' in stale
        aligned=await review(dict(id=2,text='SHORT? #X Aligned in 3 sec',timestamp=100))
        assert 'не подтверждение прибыли' in aligned
        text='LONG? #X\n#ETHEREUM\n0x'+'1'*40
        verdict=await review(dict(id=3,text=text,timestamp=100))
        assert 'ВХОД НЕ ПОДТВЕРЖДЁН' in verdict and 'заранее на DEX' in verdict
        assert 'https://t.me/MexcDexSpreadTracker/3' in verdict
    asyncio.run(go())


def test_market_links_do_not_allow_script_or_untrusted_post_urls():
    assert market_link('other','X','javascript:alert(1)')==(None,False)
    assert market_link('other','X','https://user:pass@host')==(None,False)
    assert market_link('gate','X')[0]=='https://www.gate.com/trade/X_USDT'


def test_channel_model_success_is_never_a_live_recommendation(monkeypatch):
    import app.dex_paired_experiment as paired
    async def go():
        address='0x'+'1'*40
        route=dict(base='X',chain_id=1,asset=address)
        async def discovery(*args):pass
        bundle=NS(route_discovery=NS(signal=discovery),dex_experiments={'dex_channel':NS(source=NS(routes=[route]))})
        answers=[dict(ok=True,position=dict(entry_cash=25,base_qty=1,entry_price=26.5,reserve=57,
                    entry_immediate_net=-.3,paired_policy=dict(raw_edge=1.5,costs=.2,funding_reserve=.01,
                    partial_net=.54,target_usd=.54,stop_usd=.5,max_seconds=1200)))]
        class Source:
            def __init__(self,*args):pass
            async def entry(self,route):return answers[0]
        monkeypatch.setattr(paired,'PairedSource',Source)
        review=Reviewer(bundle,clock=lambda:100)
        post=dict(id=1,text='SHORT? #X\n#ETHEREUM\n'+address,timestamp=100)
        t=await review(post)
        assert 'LIVE-вход не подтверждён' in t and 'NET модели $+0.5400' in t
        assert 'Доход зависит от будущего схождения' in t
        assert not hasattr(bundle,'positions')
        answers[0]['position']['paired_policy']['raw_edge']=1
        t=await review(post)
        assert 'меньше 5%' in t and 'ВХОД НЕ ПОДТВЕРЖДЁН' in t
    asyncio.run(go())


def test_route_rotation_survives_cooldown_bucket_and_old_spot_is_not_resent(tmp_path):
    async def go():
        now=[100];seen=[];sent=[]
        async def notify(text):sent.append(text)
        s=Scanner(tmp_path/'db',notify,clock=lambda:now[0],venues=[])
        await s.init()
        s.candidates=lambda:[(10-i,'X'+str(i)+'/USDT','a','b') for i in range(50)]
        async def evaluate(symbol,*args):seen.append(symbol);return None
        s.evaluate=evaluate
        await s.cycle()
        now[0]=301
        await s.cycle()
        assert seen==['X0/USDT','X1/USDT']
        await s.store.enqueue('old','spot',{})
        now[0]=400
        await s.flush()
        assert not sent and not await s.store.pending()
    asyncio.run(go())
