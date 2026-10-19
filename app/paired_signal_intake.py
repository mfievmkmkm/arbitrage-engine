"""Channel candidate routing for strategy A. Never opens an order or position."""
import asyncio
import json
import time
import aiosqlite
from .dex_signal_focus import parse_signal
from .dex_paired_experiment import NATIVE
from .dex_virtual_paper_source import route_key, routes_checked
from .decision_journal import Journal


class Intake:
    def __init__(self,path,discovery,service,clock=time.time):
        self.path,self.discovery,self.service,self.clock=str(path),discovery,service,clock
        self.lock=asyncio.Lock();self.journal=Journal(path,clock)

    async def persist(self,post,result,trace):
        result['trace_id']=trace
        async with aiosqlite.connect(self.path) as db:
            await db.execute('INSERT OR REPLACE INTO paired_channel_intake VALUES(?,?,?,?)',
                (post['id'],self.clock(),'READY' if result['ok'] else 'SKIP',json.dumps(result,ensure_ascii=False)))
            await db.commit()
        await self.journal.finish(trace,'PRIORITIZED' if result['ok'] else 'REJECTED',result['reason'],
                                  {'venues':result.get('venues',{})})
        return result

    async def accept(self,post):
        started=time.perf_counter()
        async with self.lock:
            await self.journal.init()
            async with aiosqlite.connect(self.path) as db:
                await db.execute('CREATE TABLE IF NOT EXISTS paired_channel_intake(message_id INTEGER PRIMARY KEY,ts REAL,status TEXT,payload TEXT)')
                old=await (await db.execute('SELECT payload FROM paired_channel_intake WHERE message_id=?',(post['id'],))).fetchone()
                await db.commit()
                if old and json.loads(old[0]).get('ok'):return dict(json.loads(old[0]),duplicate=True)
            trace=await self.journal.start('A','channel:'+str(post['id']),'DEX','MEXC/Bybit','observe','channel',
                                           {'message_id':post['id'],'signal_time':post['timestamp']})
            failure=None;signal=None
            if 'Aligned' in post['text']:failure='ALIGNED_IS_ANALYSIS_REPORT'
            elif not 0<=self.clock()-post['timestamp']<=90:failure='CHANNEL_SIGNAL_EXPIRED'
            else:
                try:signal=parse_signal(post['text'])
                except ValueError:failure='CHANNEL_TOKEN_OR_CHAIN_UNPARSEABLE'
            if signal and signal['chain_id'] not in NATIVE:failure='PAIRED_SIGNAL_CHAIN_UNSUPPORTED'
            if signal and signal['side']=='buy':failure='DEX_SHORT_OR_INVENTORY_UNAVAILABLE'
            if failure:return await self.persist(post,dict(ok=False,reason=failure),trace)
            found=[];outcomes={}
            resolver=getattr(getattr(getattr(self.service,'scanner',None),'discovery',None),'resolve_signal',None)
            # Prefer the signal venue, then independently check the other venue.
            for venue in ('mexc','bybit'):
                try:
                    if resolver:
                        route=await asyncio.wait_for(resolver(venue,signal),20)
                    else:
                        await asyncio.wait_for(self.discovery.ensure_evm(venue,signal),20)
                        engine=self.discovery.engines['dex_channel' if venue=='mexc' else 'dex_bybit']
                        routes=engine.source.routes
                        exact=[r for r in routes if r['cex_venue']==venue and r['base']==signal['base']
                               and r['chain_id']==signal['chain_id'] and r['asset'].lower()==signal['asset'].lower()]
                        if len(exact)!=1:raise ValueError('PAIRED_SIGNAL_ROUTE_AMBIGUOUS')
                        route=dict(exact[0],mode='paired',notional_usd=100,max_notional_usd=110,
                                   native_symbol=NATIVE[signal['chain_id']]+'/USDT:USDT',native_decimals=18)
                    if not 0<=self.clock()-post['timestamp']<=90:raise ValueError('CHANNEL_SIGNAL_EXPIRED')
                    routes_checked([route],300)
                    routes=self.service.paper.source.routes
                    if any(r['cex_venue']==venue and r['base']==route['base'] and r['chain_id']==route['chain_id']
                           and r['asset'].lower()!=route['asset'].lower() for r in routes):
                        raise ValueError('DEX_SCAN_CONTRACT_CONFLICT')
                    route.update(signal_source='channel',signal_id=post['id'],signal_deadline=post['timestamp']+90)
                    if not hasattr(self.service,'scanner'):
                        if len(routes)>=32:raise ValueError('DEX_ACTIVE_ROUTE_LIMIT')
                        if route_key(route) not in {route_key(r) for r in routes}:routes.append(route)
                    self.service.prioritize(route)
                    found.append(venue);outcomes[venue]='PRIORITIZED'
                except asyncio.CancelledError:raise
                except Exception as exc:
                    outcomes[venue]=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
            result=dict(ok=bool(found),reason='EXACT_ROUTES_PRIORITIZED' if found else 'NO_VERIFIED_FUTURES_DEX_ROUTE',
                        base=signal['base'],venues=outcomes,elapsed_ms=round((time.perf_counter()-started)*1000))
            return await self.persist(post,result,trace)
