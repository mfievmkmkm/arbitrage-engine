"""Independent bounded PAPER discovery. Never calls an engine or an order API."""
import asyncio
import time
from collections import Counter
from .dex_request_budget import background
from .dex_virtual_paper_source import route_key, routes_checked


class Scanner:
    enabled_name = 'dex_paired'
    interval = .25

    def __init__(self, service, workers=3, timeout=15):
        self.service = service
        self.workers = workers
        self.timeout = timeout
        self.entry_enabled = True
        self.wake = asyncio.Event()
        self.catalog = {}
        self.priority = []
        self.index = 0
        self.checked = 0
        self.reasons = Counter()
        self.last_batch_seconds = None
        self.last_batch_at = None
        self.last_reasons = Counter()
        self.provider_health = {}
        self.started = time.monotonic()
        self.last_sweep_seconds = None
        self.sweep_seen = set()
        self.sweep_started = time.monotonic()
        self.cooldowns={}

    def add(self, route, priority=False):
        routes_checked([route], 300)
        key = route_key(route)
        if key not in self.catalog and len(self.catalog) >= 256:
            raise ValueError('DEX_SCAN_CATALOG_LIMIT_256')
        if any(r['cex_venue']==route['cex_venue'] and r['chain_id']==route['chain_id'] and r['base']==route['base']
               and r['asset'].lower()!=route['asset'].lower() for r in self.catalog.values()):
            raise ValueError('DEX_SCAN_CONTRACT_CONFLICT')
        self.catalog[key] = dict(route)
        if priority and key not in self.priority:
            self.priority.append(key)
        if priority:
            self.cooldowns.pop(key,None)
            self.wake.set()

    def promote(self, route):
        """Keep executable inventory routes bounded; never evict an open position."""
        source = self.service.paper.source
        key = route_key(route)
        if any(route_key(r)==key for r in source.routes):
            return
        protected = {tuple(p.get('virtual_route_key', ())) for p in self.service.paper.positions.values()}
        now = self.service.paper.source.clock() if hasattr(self.service.paper.source, 'clock') else 0
        protected.update(k for k,c in self.service.candidates.items()
                         if 0 <= now-c.get('position', {}).get('opened_at', -1000) <= 30)
        if len(source.routes) >= 32:
            if getattr(self.service.paper, 'lock', None) and self.service.paper.lock.locked():
                raise ValueError('DEX_ENTRY_OR_MONITOR_IN_PROGRESS')
            old = next((r for r in source.routes if route_key(r) not in protected), None)
            if old is None:
                raise ValueError('DEX_ACTIVE_ROUTE_LIMIT')
            source.routes.remove(old)
            self.service.candidates.pop(route_key(old), None)
        source.routes.append(dict(route))
        self.service.routes = source.routes

    async def check(self, route):
        from .dex_paired_experiment import PairedSource
        key = route_key(route)
        original = self.service.paper.source
        previous=self.service.candidates.get(key)
        journal=getattr(original,'journal',None)
        if previous and journal and previous['position'].get('trace_id'):
            await journal.finish(previous['position']['trace_id'],'SUPERSEDED','CANDIDATE_QUOTES_REFRESHED')
        route = dict(route)
        if route.get('signal_deadline',original.clock()+1)<original.clock():
            # The asset remains in the independent universe, not a stale signal.
            route={k:v for k,v in route.items() if k not in ('signal_source','signal_id','signal_deadline')}
        if hasattr(self.service.paper, 'operator_notional'):
            limit = self.service.paper.operator_notional()
            route.update(notional_usd=min(100, limit/1.1), max_notional_usd=limit)
        source = PairedSource(original, [route])
        source.identity_gate = original.identity_gate
        source.entry_venues = {'bybit','mexc'}
        source.blocked_until = original.blocked_until
        token = background.set(True)
        try:
            result = await asyncio.wait_for(source.entry(route), self.timeout)
            candidate = getattr(source, 'last_candidate', None)
            fee_source=candidate['position'].get('fee_source') if candidate else None
            fee_ok=fee_source=='ACCOUNT_READ_ONLY' or (route['cex_venue']=='mexc' and fee_source=='MEXC_API_FUTURES_PUBLIC_SCHEDULE_PAPER')
            if result.get('ok') and candidate and fee_ok:
                if not 0 <= original.clock()-candidate['position']['opened_at'] <= 30:
                    result = dict(ok=False, reason='DEX_SCAN_CANDIDATE_STALE')
                else:
                    self.promote(route)
                    self.service.candidates[key] = candidate
                    self.service.wake.set()
                    return candidate, 'READY'
            self.service.candidates.pop(key, None)
            reason=result.get('reason', 'PAIRED_ACCOUNT_FEE_REQUIRED')
            delays={'KYBER_FILTERED_LIQUIDITY':30,'DEX_TAX_CHAIN_UNSUPPORTED':300,
                    'DEX_TAX_HTTP_404':60,'DEX_TAX_HONEYPOT_OR_UNKNOWN':60,'DEX_TAX_FLAGS_UNSUPPORTED':60,
                    'DEX_TAX_HONEYPOT_DETECTED':300,'DEX_TAX_HONEYPOT_UNKNOWN':60,
                    'DEX_TAX_RISK_UNKNOWN':60,'DEX_TAX_RISK_HIGH':300,'DEX_TAX_SIMULATION_FAILED':30,
                    'DEX_CEX_IDENTITY_UNVERIFIED':30,'DEX_VIRTUAL_AMOUNT_BELOW_NATIVE_STEP':60,
                    'KYBER_CHAIN_UNSUPPORTED':300,'DEX_HTTP_400':15}
            if reason in delays:self.cooldowns[key]=original.clock()+delays[reason]
            trace=getattr(source,'current_trace',None)
            if trace and getattr(source,'journal',None) and result.get('ok'):
                await source.journal.finish(trace,'REJECTED',reason,{'fee_source':fee_source})
            return None, reason
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.service.candidates.pop(key, None)
            reason='DEX_SCAN_TIMEOUT' if isinstance(exc, asyncio.TimeoutError) else (str(exc) if isinstance(exc,ValueError) else type(exc).__name__)
            trace=getattr(source,'current_trace',None)
            if trace and getattr(source,'journal',None):
                await source.journal.finish(trace,'REJECTED',reason,{'error_type':type(exc).__name__})
            return None, reason
        finally:
            background.reset(token)

    async def cycle(self):
        evm=getattr(self.service.paper.source.provider,'evm',None)
        self.provider_health=evm.health() if hasattr(evm,'health') else {}
        if self.provider_health.get('blocked'):
            journal=getattr(self.service.paper.source,'journal',None)
            if journal:
                for candidate in self.service.candidates.values():
                    trace=candidate['position'].get('trace_id')
                    if trace:await journal.finish(trace,'REJECTED',self.provider_health.get('reason') or 'PROVIDER_BLOCKED',{'stage':'candidate_waiting'})
            self.service.candidates.clear()
            self.interval=min(30,max(1,self.provider_health['retry_after_seconds']))
            return []
        self.interval=.25
        self.service.sync()
        for r in list(self.service.routes):
            if r['cex_venue'] in ('bybit','mexc'):
                self.add(r)
        now=self.service.paper.clock()
        keys = [k for k in self.catalog if self.cooldowns.get(k,0)<=now]
        if not keys and not self.priority:
            next_retry=min(self.cooldowns.values(),default=now+1)
            self.interval=max(.25,min(30,next_retry-now))
            return []
        selected = []
        while self.priority and len(selected)<self.workers:
            key = self.priority.pop(0)
            if key in self.catalog and key not in selected:
                selected.append(key)
        for _ in range(len(keys)):
            if len(selected)>=self.workers:
                break
            key = keys[self.index % len(keys)]
            self.index += 1
            if key not in selected:
                selected.append(key)
        started = time.monotonic()
        results = await asyncio.gather(*(self.check(self.catalog[k]) for k in selected))
        self.last_batch_seconds = time.monotonic()-started
        self.last_batch_at = self.service.paper.clock()
        self.provider_health=evm.health() if hasattr(evm,'health') else {}
        self.checked += len(results)
        self.last_reasons = Counter(reason for _, reason in results)
        self.reasons.update(self.last_reasons)
        if getattr(self.service.paper,'path',None) and self.last_batch_at-getattr(self,'last_audit_at',0)>=5:
            from .scan_audit import save
            await save(self.service.paper.path,'paired',dict(ts=self.last_batch_at,checked=self.checked,
                routes=len(self.catalog),batch_seconds=self.last_batch_seconds,
                reasons=dict(self.reasons),provider=self.provider_health,
                latest=[dict(route=self.catalog[k],reason=reason) for k,(_,reason) in zip(selected,results)]))
            self.last_audit_at=self.last_batch_at

        if time.monotonic()-getattr(self, "last_diagnostic_log", 0)>=60:
            import logging
            logging.getLogger("arbitrage").info(
                "DEX_SCAN | routes=%s | venues=%s | checked=%s | batch_ms=%.0f | reasons=%s | provider=%s | quotes=%s | access_code=%s",
                len(self.catalog), {v:sum(r['cex_venue']==v for r in self.catalog.values()) for v in ('bybit','mexc')}, self.checked, self.last_batch_seconds*1000, dict(self.last_reasons), self.provider_health.get('provider','0x'),
                self.provider_health.get('responses_ok',0),
                getattr(getattr(self.service.paper.source.provider, 'evm', None), 'access_error_code', None))
            self.last_diagnostic_log = time.monotonic()
        self.sweep_seen.update(selected)
        if keys and set(keys)<=self.sweep_seen:
            self.last_sweep_seconds = time.monotonic()-self.sweep_started
            self.sweep_started = time.monotonic()
            self.sweep_seen.clear()
        now = self.service.paper.clock()
        journal=getattr(self.service.paper.source,'journal',None)
        if journal:
            for c in self.service.candidates.values():
                trace=c['position'].get('trace_id')
                if trace and now-c['position']['opened_at']>30:
                    await journal.finish(trace,'EXPIRED','CANDIDATE_EXPIRED_BEFORE_EXECUTION')
        self.service.candidates = {k:c for k,c in self.service.candidates.items()
                                   if 0<=now-c['position']['opened_at']<=30}
        if self.service.notify_candidate and not self.service.paper.positions:
            candidates = sorted((c for c,_ in results if c),
                                key=lambda c:c['position']['paired_policy']['partial_net'], reverse=True)
            if candidates:
                await self.service.notify_candidate(candidates[0])
        return []
