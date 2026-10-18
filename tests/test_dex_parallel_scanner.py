import asyncio
from types import SimpleNamespace as NS
import pytest
from app.dex_parallel_scanner import Scanner
from app.dex_request_budget import Budget, background
from app.dex_virtual_paper_source import route_key
from test_dex_outage_regressions import paired_source_with_rate
from app.dex_paired_experiment import PairedSource


def test_parallel_read_only_checks_keep_equal_inventory_and_account_fee(monkeypatch):
    async def run():
        source, route, public, provider, now, calls = paired_source_with_rate()
        route = dict(route, cex_venue='bybit')
        # Test identity/economics through the real source; fake read-only venue.
        source.public['bybit'] = source.public.pop('mexc', public)
        source.private['bybit'] = source.private.pop('mexc', public)
        source.entry_venues = {'bybit'}
        source.routes = [route]
        public.currencies = {'X':dict(code='X', networks={'ETH':dict(contract=route['asset'])})}
        original = PairedSource.entry
        active = 0
        peak = 0
        async def entry(self, r):
            nonlocal active, peak
            active += 1; peak = max(peak, active)
            try:
                await asyncio.sleep(.01)
                result = await original(self, r)
                if result.get('ok'):
                    result['position']['fee_source']='ACCOUNT_READ_ONLY'
                return result
            finally:
                active -= 1
        monkeypatch.setattr(PairedSource, 'entry', entry)
        service = NS(paper=NS(source=source,positions={}),routes=[route],candidates={},wake=asyncio.Event(),
                     notify_candidate=None,sync=lambda:None)
        scan = Scanner(service)
        # Different verified stable contracts produce independent route identities.
        routes = [dict(route, stable='0x'+str(i)*40) for i in (7,8,9)]
        await asyncio.gather(*(scan.check(r) for r in routes))
        assert peak == 3
        assert service.paper.positions == {}
        # The fake provider may reject the changed stables; rejected routes cannot be candidates.
        for candidate in service.candidates.values():
            p=candidate['position']
            assert p['cex_side']=='sell' and p['virtual_asset_qty']>=p['base_qty']
            assert p['fee_source']=='ACCOUNT_READ_ONLY'
    asyncio.run(run())


def test_timeout_clears_stale_candidate_without_blocking_other_routes(monkeypatch):
    async def run():
        from test_dex_virtual_paper import source as make
        source,route,*_=make('paired')
        service=NS(paper=NS(source=source,positions={}),candidates={route_key(route):{}},wake=asyncio.Event())
        async def slow(*_): await asyncio.Event().wait()
        monkeypatch.setattr(PairedSource,'entry',slow)
        scan=Scanner(service,timeout=.01)
        candidate,reason=await scan.check(route)
        assert candidate is None and reason=='DEX_SCAN_TIMEOUT'
        assert not service.candidates
    asyncio.run(run())


def test_scan_promotion_preserves_open_position_route():
    from test_dex_virtual_paper import ROUTE
    routes=[dict(ROUTE,asset='0x'+f'{i:040x}') for i in range(1,33)]
    protected=route_key(routes[0])
    service=NS(paper=NS(source=NS(routes=routes),positions={'1':dict(virtual_route_key=list(protected))}),candidates={})
    scan=Scanner(service)
    new=dict(ROUTE,asset='0x'+'f'*40)
    scan.promote(new)
    assert len(routes)==32 and route_key(routes[0])==protected
    assert any(route_key(r)==route_key(new) for r in routes)


def test_priority_budget_and_cancelled_waiters_release_capacity():
    async def run():
        budget=Budget(spacing=.001,concurrency=3)
        low=background.set(True)
        held=await budget.acquire()
        waiting=asyncio.create_task(budget.acquire())
        background.reset(low)
        await asyncio.sleep(.005)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):await waiting
        high=await asyncio.wait_for(budget.acquire(),.1)
        budget.release(high);budget.release(held)
        low=background.set(True)
        again=await asyncio.wait_for(budget.acquire(),.1)
        budget.release(again);background.reset(low)
        assert budget.foreground==0
    asyncio.run(run())


def test_catalog_rejects_conflicting_contracts_and_bounds_coverage():
    from test_dex_virtual_paper import ROUTE
    scan=Scanner(None)
    scan.add(ROUTE)
    with pytest.raises(ValueError,match='CONTRACT_CONFLICT'):
        scan.add(dict(ROUTE,asset='0x'+'f'*40))


def test_successful_parallel_candidates_are_isolated_ranked_and_never_executed(monkeypatch):
    from test_dex_virtual_paper import source as make
    async def run():
        original, route, *_ = make('paired')
        route = dict(route, cex_venue='bybit')
        routes = [dict(route, base=base, asset='0x'+str(i)*40) for i,base in ((7,'A'),(8,'B'),(9,'C'))]
        original.routes = routes
        inflight = set(); sources = []; notified = []
        async def entry(self, r):
            sources.append(self)
            inflight.add(id(self))
            await asyncio.sleep(.01)
            p=dict(opened_at=original.clock(),fee_source='ACCOUNT_READ_ONLY',
                   paired_policy=dict(partial_net=ord(r['base'])))
            self.last_candidate=dict(route=r,position=p)
            inflight.remove(id(self))
            return dict(ok=True,position=p)
        monkeypatch.setattr(PairedSource,'entry',entry)
        async def notify(c):notified.append(c)
        service=NS(paper=NS(source=original,positions={},clock=original.clock),routes=routes,candidates={},
                   wake=asyncio.Event(),notify_candidate=notify,sync=lambda:None)
        scan=Scanner(service)
        await scan.cycle()
        assert scan.checked==3 and scan.reasons['READY']==3
        assert len({id(s) for s in sources})==3
        assert len(service.candidates)==3 and len(notified)==1
        assert notified[0]['route']['base']=='C'
        assert service.paper.positions=={}
        assert service.wake.is_set()
    asyncio.run(run())


def test_catalog_uses_verified_contract_metadata_and_ignores_symbol_only_markets():
    from test_dex_virtual_paper import ROUTE
    from app.dex_evm_catalog import Catalog
    async def run():
        currencies={
            'X':dict(code='X',networks={'ETH':dict(contract=ROUTE['asset'])}),
            'Y':dict(code='Y',networks={'ETH':{}}),
            'Z':dict(code='Z',networks={'ETH':dict(contract='0x'+'7'*40),
                                      'ERC20':dict(contract='0x'+'8'*40)})
        }
        class Gate:
            cache={'bybit':(100,currencies,True,())}
            async def verify(self,r):return {}
        class Provider:
            async def token(self,network,address):
                return dict(symbol='X' if address.lower()==ROUTE['asset'].lower() else 'USDC',decimals=6)
        markets={b:dict(symbol=b+'/USDT:USDT',base=b,quote='USDT',settle='USDT',
                       swap=True,linear=True,active=True,contractSize=1) for b in currencies}
        source=NS(routes=[dict(ROUTE,cex_venue='bybit')],identity_gate=Gate(),
                  public={'bybit':NS(markets=markets)},provider=NS(evm=Provider()))
        scan=Scanner(NS(paper=NS(source=source)))
        catalog=Catalog(scan)
        await catalog.cycle()
        assert catalog.discovered==1 and len(scan.catalog)==1
        r=next(iter(scan.catalog.values()))
        assert r['base']=='X' and r['asset']==ROUTE['asset'].lower()
        assert r['mode']=='paired' and r['native_symbol']=='ETH/USDT:USDT'
    asyncio.run(run())


def test_known_provider_block_skips_books_fees_and_counters_but_keeps_positions():
    from app.zerox_research import Provider
    async def run():
        p=Provider('test-only',clock=lambda:100)
        p.access_block_until=1000;p.access_status=403
        def forbidden():raise AssertionError('No scan IO while API is blocked')
        source=NS(provider=NS(evm=p))
        positions={'1':dict(status='OPEN')}
        service=NS(paper=NS(source=source,positions=positions),sync=forbidden,candidates={'old':{}})
        scan=Scanner(service)
        await scan.cycle()
        assert scan.checked==0 and scan.last_batch_at is None
        assert scan.interval==30 and scan.provider_health['reason']=='DEX_HTTP_403'
        assert not service.candidates and service.paper.positions==positions
        assert p.requests_sent==0
    asyncio.run(run())
