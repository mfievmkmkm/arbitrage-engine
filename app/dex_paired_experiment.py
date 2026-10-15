"""Isolated two-leg virtual DEX BUY / CEX SHORT; no wallet or order writes."""
import json
import math
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
        self.focus = self.signals_only = False
        self.native_paired_inventory = True
        self.identity_gate = Gate(self.public, self.private, self.clock)

    async def entry(self, route):
        try:
            evidence = await self.identity_gate.verify(route)
        except ValueError as exc:
            return dict(ok=False, reason=str(exc))
        candidate=dict(route)
        attempts=[]
        for _ in range(3):
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
        # The DEX asset cannot be wished away: residual rounding is recorded.
        raw = p['base_qty'] * p['entry_price'] - p['entry_cash']
        costs = max(raw - p['entry_edge'], -p['entry_immediate_net'])
        try:
            rate=float(p['funding_rate_assumption'])
        except (TypeError,ValueError):
            return dict(ok=False,reason='PAIRED_FUNDING_CALENDAR_UNKNOWN')
        if not math.isfinite(rate) or abs(rate)>=1:
            return dict(ok=False, reason='PAIRED_FUNDING_CALENDAR_UNKNOWN')
        # SHORT pays when the public rate is negative. Positive carry is not
        # used to qualify a spread entry and is not credited in advance.
        count = len(calendar(p, p['opened_at'] + 1200))
        reserve = max(0, -rate) * count * p['base_qty'] * p['entry_price']
        try:
            policy = plan(p['entry_cash'], raw, costs, reserve)
        except ValueError as exc:
            return dict(ok=False, reason=str(exc), diagnostics=dict(entry_edge=p['entry_edge']))
        if -p['entry_immediate_net'] >= policy['stop_usd']:
            return dict(ok=False, reason='PAIRED_IMMEDIATE_COST_HIGH')
        p.update(paired_policy=policy, cex_identity=evidence,
                 strategy='dex_paired', hedge_residual_qty=p['virtual_asset_qty']-p['base_qty'],
                 paired_route=dict(route),native_size_attempts=attempts)
        return result


class PairedCycle(Cycle):
    def __init__(self, engine, legacy):
        super().__init__(engine, engine.source.routes)
        self.interval = 5
        self.legacy = legacy
        self.unsupported = {}

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
        if hasattr(self.paper, 'operator_notional'):
            limit = self.paper.operator_notional()
            for route in self.routes:
                route.update(notional_usd=min(100, limit / 1.1), max_notional_usd=limit)
        original = self.entry_enabled
        state = self.paper.state
        if state.get('day') == int(self.paper.clock()//86400) and state.get('day_net',0) <= -5:
            self.entry_enabled = False
        try:
            return await super().cycle()
        finally:
            self.entry_enabled = original


async def build(bundle, runtime, db_path, notify, allowed, notify_open=None):
    original = bundle.dex_paper.source
    # Seed a genuine paired route even when the legacy watchlist is Solana-only.
    routes = [dict(default_routes('paired', 100)[0], max_notional_usd=110)]
    source = PairedSource(original, routes)
    engine = Engine(db_path, source, capital=250, virtual=True, namespace='paired',
                    clock=source.clock, min_edge=.25, max_seconds=1200)
    await engine.init()
    engine.budget = lambda: max(0, engine.capital + engine.state.get('realized_net', 0))
    engine.allow_open = lambda p: runtime.enabled.get('dex_paired', False) and allowed(p) and (
        engine.state.get('day') != int(engine.clock()//86400) or engine.state.get('day_net', 0) > -5)
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
    return engine
