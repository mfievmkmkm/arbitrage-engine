"""Controlled regressions. No claims of market fills or profitable signals."""
import asyncio
from types import SimpleNamespace as NS
import pytest


def test_spot_only_asset_enters_b_universe_without_futures_listing(tmp_path):
    from test_dex_transfer_paper import setup
    from app.dex_transfer_router import Router
    from app.dex_spot_catalog import Catalog
    async def go():
        feed,old,_,prices,now=await setup(tmp_path)
        old.paired.scanner.catalog={}
        prices.token=lambda network,asset:asyncio.sleep(0,result={'symbol':'X','decimals':6})
        router=Router(feed,old.paired)
        catalog=Catalog(router)
        assert not router.catalog()
        await catalog.cycle()
        routes=router.handlers['bybit'].catalog()
        assert len(routes)==1 and routes[0]['cex_symbol']=='X/USDT'
        assert routes[0]['discovery_source']=='INDEPENDENT_SPOT_UNIVERSE'
        assert catalog.possible=={'bybit':1}
        await feed.store.set_control('mode','auto')
        await feed.store.set_control('budget',100)
        await feed.store.set_control('min_spread',2)
        router.handlers['bybit'].index=1
        await router.cycle()
        assert feed.paper.state['position']
        assert feed.paper.state['position']['signal']['buy']=='dex56'
        # No ticker-derived futures route was inserted into strategy A.
        assert not old.paired.scanner.catalog
    asyncio.run(go())


def test_spot_catalog_rejects_conflicting_contracts_and_wrong_metadata(tmp_path):
    from test_dex_transfer_paper import setup,ASSET
    from app.dex_spot_catalog import Catalog
    from app.dex_transfer_router import Router
    async def go():
        feed,old,client,prices,_=await setup(tmp_path)
        old.paired.scanner.catalog={}
        currency=client.currencies['X']
        currency['networks']['BEP20']=dict(currency['networks']['BSC'],contract='0x'+'9'*40)
        assert Catalog.possibilities(client,client.currencies)==[]
        del currency['networks']['BEP20']
        prices.token=lambda *args:asyncio.sleep(0,result={'symbol':'WRONG','decimals':6})
        router=Router(feed,old.paired);catalog=Catalog(router)
        await catalog.cycle()
        assert not router.catalog()
        assert catalog.last_reason=='DEX_SPOT_CATALOG_METADATA_MISMATCH'
    asyncio.run(go())


def test_gas_entry_cap_must_not_block_pricing_existing_exit():
    from test_dex_virtual_paper import source
    async def go():
        s,r,_,provider,_=source('paired')
        entry=await s.entry(r);assert entry['ok'],entry
        original=provider.price
        async def expensive(*args):
            q=await original(*args);q['network_fee_raw']='1000000000000000';return q
        provider.price=expensive
        exit_quote=await s.exit(entry['position'])
        assert exit_quote['ok'] and exit_quote['gas']>.25
        refused=await s.entry(r)
        assert refused['reason']=='DEX_VIRTUAL_GAS_BUDGET_LIMIT'
        assert refused['diagnostics']['observed']>.25
        assert refused['diagnostics']['threshold']==.25
    asyncio.run(go())


def test_current_journal_report_includes_actual_numbers_not_only_codes(tmp_path):
    from app.decision_journal import Journal
    from app.entry_evidence_report import inspect,render
    async def go():
        j=Journal(tmp_path/'db',clock=lambda:10000);await j.init()
        trace=await j.start('A','X/USDT','dex1','bybit','paper_auto')
        await j.finish(trace,'REJECTED','DEX_VIRTUAL_GAS_BUDGET_LIMIT',
                       {'observed':.81,'threshold':.25,'unit':'USD','gas_usd':.81,'untrusted':'secret'})
        report=await inspect(j)
        assert report['examples'][0]['facts']['observed']==.81
        assert 'untrusted' not in str(report)
        assert '0.81' in render(report) and report['limits_changed'] is False
        assert len(render(report))<4096
    asyncio.run(go())


def test_transfer_reservation_is_not_reported_as_executed_buy(tmp_path):
    from test_dex_transfer_paper import setup
    async def go():
        feed,service,_,_,now=await setup(tmp_path)
        e=await service.review('X/USDT','dex56','bybit')
        await feed.paper.open(dict(signal=e,transfer=e['transfer']))
        before=await feed.journal.snapshot()
        assert not [r for r in before['stages'] if r[1]=='EXECUTED']
        for _ in range(6):
            p=feed.paper.state['position']
            if p['fills']:break
            now[0]=max(now[0],p['due']);await feed.paper.monitor(feed)
        after=await feed.journal.snapshot()
        assert [r[2] for r in after['stages'] if r[1]=='EXECUTED']==[1]
    asyncio.run(go())
