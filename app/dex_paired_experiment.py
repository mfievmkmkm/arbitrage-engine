"""Isolated two-leg virtual DEX BUY / CEX SHORT; no wallet or order writes."""
import json
import math
import asyncio
import copy
import os
from .dex_virtual_paper_source import Source, route_key
from .dex_cex_identity import Gate
from .cex_dex_paper import Engine, Cycle
from .paired_paper_policy import plan
from .configure_virtual_paper import default_routes
from .cex_dex_paper_source import calendar

# Native currency is used for gas, never inferred from the traded token ticker.
NATIVE = {1:'ETH', 10:'ETH', 42161:'ETH', 8453:'ETH', 59144:'ETH',
          534352:'ETH', 57073:'ETH', 130:'ETH', 480:'ETH',
          56:'BNB', 137:'POL', 43114:'AVAX', 5000:'MNT', 146:'S',
          80094:'BERA', 143:'MON'}


class PairedSource(Source):
    def __init__(self, original, routes):
        super().__init__(original.provider, routes, original.public, original.fs,
                         original.private, original.clock, max_notional=300)
        self.price_providers = getattr(original, "price_providers", {})
        self.focus = self.signals_only = False
        self.native_paired_inventory = True
        self.identity_gate = Gate(self.public, self.private, self.clock)
        self.journal=getattr(original,'journal',None)
        self.operator_profile=getattr(original,'operator_profile',lambda:{})
        self.fee_cache=getattr(original,'fee_cache',{})
        self.fee_locks=getattr(original,'fee_locks',{})

    async def exit(self, p):
        # Provider identity survives a restart/configuration change. A legacy
        # position is never silently marked against a new quote provider.
        name = p.get("paired_quote_provider", (p.get("entry_dex") or {}).get("provider", "0x"))
        if self.price_providers:
            provider = self.price_providers.get(name)
            if provider is None:
                return dict(ok=False, reason="PAIRED_POSITION_PROVIDER_UNAVAILABLE")
            view = copy.copy(self)
            view.provider = provider
            return await Source.exit(view, p)
        return await Source.exit(self, p)

    def route(self,p):
        route=super().route(p)
        return dict(route,slippage_bps=p.get('paired_route',{}).get('slippage_bps',route.get('slippage_bps',20)))

    async def entry(self, route):
        from .preflight_progress import current, snapshot
        settings=self.operator_profile()
        trace=route.get('_execution_trace')
        if self.journal:
            mode=settings.get('mode','observe')
            if not trace:
                trace=await self.journal.start('A',route['cex_symbol'],'dex'+str(route['chain_id']),route['cex_venue'],
                    'observe' if mode=='observe' else 'paper_'+mode,
                    source=route.get('signal_source','scanner'),evidence=dict(route=route,settings=settings))
            if route.get('parent_trace_id'):
                await self.journal.finish(route['parent_trace_id'],'RECHECKED','FRESH_EXECUTION_RECHECK',{'child_trace_id':trace})
        self.current_trace=trace
        progress_token=current.set(dict(stage='IDENTITY'))
        try:
            route=dict(route,slippage_bps=settings.get('slippage_bps',route.get('slippage_bps',20)))
            self.reader.max_slippage=route['slippage_bps']/10000
            primary=self.provider
            attempts=[]
            providers=[(getattr(getattr(primary,'evm',None),'name','primary'),primary)]
            for name,provider in self.price_providers.items():
                evm=getattr(provider,'evm',provider)
                if provider is primary or (name=='0x' and not getattr(evm,'api_key',None)):continue
                if hasattr(evm,'health') and evm.health().get('blocked'):continue
                providers.append((name,provider))
            try:
                for name,provider in providers[:2]:
                    self.provider=provider
                    result=await self._paired_entry(route)
                    attempts.append(dict(provider=name,reason=result.get('reason','PREFLIGHT_OK')))
                    if result.get('ok') or result.get('reason') not in (
                        'DEX_HTTP_401','DEX_HTTP_403','DEX_HTTP_429','DEX_HTTP_500','DEX_HTTP_502','DEX_HTTP_503','DEX_HTTP_504','KYBER_PRICE_UNAVAILABLE'):
                        break
            finally:
                self.provider=primary
            result.setdefault('diagnostics',{})['provider_attempts']=attempts
            if trace:
                if result.get('ok'):
                    result['position']['trace_id']=trace
                    await self.journal.check(trace,'COSTS','PASS','PARTIAL_CONVERGENCE_NET',result['position']['paired_policy'])
                    await self.journal.finish(trace,'OBSERVED' if settings.get('mode')=='observe' else 'READY','PREFLIGHT_OK')
                else:
                    await self.journal.finish(trace,'REJECTED',result.get('reason','PAIRED_ENTRY_UNAVAILABLE'),result.get('diagnostics',{}))
            return result
        except asyncio.CancelledError:
            if trace:await self.journal.finish(trace,'REJECTED','DEX_SCAN_TIMEOUT',dict(cancelled=True,progress=snapshot()))
            raise
        except Exception as exc:
            if trace:await self.journal.finish(trace,'REJECTED',type(exc).__name__,{'error_type':type(exc).__name__})
            raise
        finally:
            current.reset(progress_token)

    async def _paired_entry(self, route):
        try:
            evidence = await self.identity_gate.verify(route)
        except ValueError as exc:
            return dict(ok=False, reason=str(exc),diagnostics=getattr(exc,'diagnostics',{}))
        if self.journal and self.current_trace:
            await self.journal.check(self.current_trace,'MATCH','PASS','FUTURES_AND_EXACT_DEX_ROUTE')
            await self.journal.check(self.current_trace,'IDENTITY','PASS','EXCHANGE_NETWORK_CONTRACT_MATCH',evidence)
        candidate=dict(route)
        attempts=[]
        for _ in range(4):
            result = await super().entry(candidate)
            attempts.append(dict(notional_usd=candidate['notional_usd'],reason=result.get('reason','PAIRED_ENTRY_OK')))
            suggested=(result.get('diagnostics') or {}).get('suggested_notional_usd')
            if (result.get('ok') or result.get('reason')!='DEX_VIRTUAL_NOTIONAL_OR_RESIDUAL_LIMIT'
                    or suggested is None or not 10 <= suggested < candidate['notional_usd']):
                break
            candidate=dict(candidate,notional_usd=suggested)
        result.setdefault('diagnostics',{})['native_size_attempts']=attempts
        if not result.get('ok'):
            return result
        p = result['position']
        if self.journal and self.current_trace:
            await self.journal.check(self.current_trace,'QUOTES','PASS','ENTRY_AND_REVERSE_QUOTES',
                                     dict(entry_cex=p['entry_cex'],entry_dex=p['entry_dex'],immediate_net=p['entry_immediate_net']))
        p["paired_quote_provider"] = (p.get("entry_dex") or {}).get("provider", "0x")
        # The DEX asset cannot be wished away: residual rounding is recorded.
        raw = p['base_qty'] * p['entry_price'] - p['entry_cash']
        costs = max(raw - p['entry_edge'], -p['entry_immediate_net'])
        if raw<0:
            return dict(ok=False,reason='DEX_SHORT_OR_INVENTORY_UNAVAILABLE',diagnostics=dict(
                dex_buy_usd=p['entry_cash'],futures_short_usd=p['base_qty']*p['entry_price'],
                raw_edge_usd=raw,required='DEX_SELL_OR_BORROWED_INVENTORY_AND_FUTURES_LONG'))
        try:
            rate=float(p['funding_rate_assumption'])
        except (TypeError,ValueError):
            return dict(ok=False,reason='PAIRED_FUNDING_CALENDAR_UNKNOWN')
        if not math.isfinite(rate) or abs(rate)>=1:
            return dict(ok=False, reason='PAIRED_FUNDING_CALENDAR_UNKNOWN')
        # SHORT pays when the public rate is negative. Positive carry is not
        # used to qualify a spread entry and is not credited in advance.
        count = len(calendar(p, p['opened_at'] + self.operator_profile().get('max_hold_seconds',1200)))
        reserve = max(0, -rate) * count * p['base_qty'] * p['entry_price']
        try:
            policy = plan(p['entry_cash'], raw, costs, reserve,self.operator_profile())
        except ValueError as exc:
            return dict(ok=False, reason=str(exc), diagnostics=dict(entry_edge=p['entry_edge'],raw_edge_usd=raw,
                costs_usd=costs,funding_reserve_usd=reserve,partial_net_usd=raw*.5-costs-reserve,
                observed=raw*.5-costs-reserve,threshold=self.operator_profile().get('min_net_usd',.25),unit='USD',settings=self.operator_profile()))
        if -p['entry_immediate_net'] >= policy['stop_usd']:
            return dict(ok=False, reason='PAIRED_IMMEDIATE_COST_HIGH',diagnostics=dict(
                observed=-p['entry_immediate_net'],threshold=policy['stop_usd'],unit='USD',
                immediate_net_usd=p['entry_immediate_net'],raw_edge_usd=raw,costs_usd=costs))
        p.update(paired_policy=policy, cex_identity=evidence,
                 strategy='dex_paired', hedge_residual_qty=p['virtual_asset_qty']-p['base_qty'],
                 paired_route=dict(route),native_size_attempts=attempts)
        self.last_candidate=dict(route=dict(route),position=p)
        return result


class PairedCycle(Cycle):
    def __init__(self, engine, legacy):
        super().__init__(engine, engine.source.routes)
        self.interval = 1
        self.legacy = legacy
        self.unsupported = {}
        self.sync_ui = None
        self.notify_candidate = None
        self.wake=asyncio.Event()
        self.priority_keys=[]
        self.candidates={}

    def prioritize(self, route):
        key=route_key(route)
        if key not in self.priority_keys:
            self.priority_keys.append(key)
        self.wake.set()
        if hasattr(self, 'scanner'):
            self.scanner.add(route, priority=True)

    def sync(self):
        source = self.paper.source
        keys = {route_key(r) for r in source.routes}
        self.unsupported = {}
        for old in self.legacy.values():
            for route in old.source.routes:
                chain = route['chain_id']
                if chain == 'solana' or chain not in NATIVE:
                    self.unsupported[str(chain)] = 'Нет полного quote-only evidence расходов для парного режима'
                    continue
                r = dict(route, mode='paired', notional_usd=100, max_notional_usd=110,
                         native_symbol=NATIVE[chain] + '/USDT:USDT', native_decimals=18)
                if route_key(r) not in keys and len(source.routes) < 32:
                    source.routes.append(r)
                    keys.add(route_key(r))
        self.routes = source.routes

    async def cycle(self):
        self.sync()
        if not self.paper.positions and self.entry_enabled and self.priority_keys:
            allowed=[r for r in self.routes if self.paper.source.venue_allowed(r['cex_venue'])]
            key=self.priority_keys.pop(0)
            for i,r in enumerate(allowed):
                if route_key(r)==key:
                    self.index=i
                    break
        if hasattr(self.paper, 'operator_notional'):
            limit = self.paper.operator_notional()
            for route in self.routes:
                route.update(notional_usd=min(100, limit / 1.1), max_notional_usd=limit)
        original = self.entry_enabled
        scanner = getattr(self, 'scanner', None)
        if scanner is not None and not self.paper.positions:
            now = self.paper.clock()
            fresh = [c for c in self.candidates.values()
                     if 0 <= now-c['position']['opened_at'] <= 30]
            if fresh and getattr(self, 'auto_enabled', lambda: True)():
                best = max(fresh, key=lambda c:c['position']['paired_policy']['partial_net'])
                allowed = [r for r in self.routes if self.paper.source.venue_allowed(r['cex_venue'])]
                self.index = next((i for i,r in enumerate(allowed)
                                   if route_key(r)==route_key(best['route'])), 0)
                if allowed and best['position'].get('trace_id'):
                    allowed[self.index]['parent_trace_id']=best['position']['trace_id']
            else:
                self.entry_enabled = False
        state = self.paper.state
        if state.get('day') == int(self.paper.clock()//86400) and state.get('day_net',0) <= -self.paper.source.operator_profile().get('daily_loss_usd',5):
            self.entry_enabled = False
        try:
            self.paper.source.last_candidate=None
            rows=await super().cycle()
            candidate=getattr(self.paper.source,'last_candidate',None)
            if candidate:
                self.candidates[route_key(candidate['route'])]=candidate
            journal=getattr(self.paper.source,'journal',None)
            if journal:
                for old in self.candidates.values():
                    trace=old['position'].get('trace_id')
                    if trace and self.paper.clock()-old['position']['opened_at']>30:
                        await journal.finish(trace,'EXPIRED','CANDIDATE_EXPIRED_BEFORE_EXECUTION')
            self.candidates={k:c for k,c in self.candidates.items()
                             if 0<=self.paper.clock()-c['position']['opened_at']<=30}
            if scanner is None and candidate and not self.paper.positions and self.entry_enabled and self.notify_candidate:
                await self.notify_candidate(candidate)
            return rows
        finally:
            self.entry_enabled = original
            for route in self.routes:route.pop('parent_trace_id',None)
            if self.sync_ui is not None:
                await self.sync_ui()


async def build(bundle, runtime, db_path, notify, allowed, notify_open=None):
    original = bundle.dex_paper.source
    # Seed a genuine paired route even when the legacy watchlist is Solana-only.
    routes = [dict(default_routes('paired', 100)[0], max_notional_usd=110)]
    source = PairedSource(original, routes)
    from .decision_journal import Journal
    source.journal=Journal(db_path,source.clock)
    await source.journal.init()
    from .asset_registry import Registry
    source.identity_gate.registry=Registry(db_path,source.clock)
    source.price_providers = {"0x": original.provider}
    selected = os.getenv("PAIRED_DEX_QUOTE_PROVIDER", "0x").lower()
    if selected not in ("0x", "kyberswap"):
        raise ValueError("PAIRED_QUOTE_PROVIDER_INVALID")
    if selected == "kyberswap" and not hasattr(original.provider, "evm"):
        raise ValueError("PAIRED_QUOTE_ROUTER_UNAVAILABLE")
    if hasattr(original.provider, "evm"):
        from .kyber_research import Provider as KyberProvider
        from .jupiter_research import Router
        from .dex_tax_evidence import Reader as TaxReader
        bundle.kyber_provider = KyberProvider(original.provider.evm, tax_reader=TaxReader())
        router = Router(bundle.kyber_provider, original.provider.solana)
        if selected == "kyberswap":
            source.provider = router
        source.price_providers["KyberSwap"] = router
    source.entry_venues={'bybit','mexc'}
    engine = Engine(db_path, source, capital=250, virtual=True, namespace='paired',
                    clock=source.clock, min_edge=.25, max_seconds=1200)
    await engine.init()
    from .paired_entry_execution import Executor
    engine.entry_executor=Executor(source,source.clock)
    engine.budget = lambda: max(0, engine.capital + engine.state.get('realized_net', 0))
    engine.allow_open = lambda p: runtime.enabled.get('dex_paired', False) and allowed(p) and (
        engine.state.get('day') != int(engine.clock()//86400) or engine.state.get('day_net', 0) > -source.operator_profile().get('daily_loss_usd',5))
    source.blocked_until = lambda r: engine.state.get('route_cooldowns', {}).get(json.dumps(route_key(r), separators=(',', ':')), 0)
    async def closed(p):
        await notify(p['symbol'], p['net'], 'Парный DEX BUY ↔ CEX SHORT • ' + p['exit_reason'])
    engine.on_closed = closed
    if notify_open:
        async def opened(p):
            await notify_open(p, 'Парный DEX BUY ↔ CEX SHORT')
        engine.on_opened = opened
    service = PairedCycle(engine, bundle.dex_experiments)
    service.sync()
    # Persist routes with positions for exact restart identity, independent of
    # a later refresh/cap of the discovery watchlist.
    for p in engine.positions.values():
        old_routes = p.get('paired_route')
        if old_routes and route_key(old_routes) not in {route_key(r) for r in source.routes}:
            from .dex_virtual_paper_source import routes_checked
            routes_checked([old_routes], 300)
            source.routes.append(old_routes)
    bundle.dex_paired = engine
    bundle.runtime.add('dex_paired', service)
    from .dex_parallel_scanner import Scanner
    service.scanner = Scanner(service)
    bundle.runtime.add('dex_paired_scan', service.scanner)
    from .dex_evm_catalog import Catalog
    service.scanner.discovery = Catalog(service.scanner)
    bundle.runtime.add('dex_paired_catalog', service.scanner.discovery)
    return engine
