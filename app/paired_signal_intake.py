"""Channel posts only identify candidates. No order, wallet or position writes."""
import asyncio
import json
import time
import aiosqlite
from .dex_signal_focus import parse_signal
from .dex_paired_experiment import NATIVE
from .dex_virtual_paper_source import route_key, routes_checked


class Intake:
    def __init__(self,path,discovery,service,clock=time.time):
        self.path,self.discovery,self.service,self.clock=str(path),discovery,service,clock
        self.lock=asyncio.Lock()

    async def accept(self,post):
        started=time.perf_counter()
        if not 0<=self.clock()-post['timestamp']<=90:
            return dict(ok=False,reason='Сигнал старше 90 секунд')
        text=post['text']
        if 'Aligned' in text:
            return dict(ok=False,reason='Это отчёт, не новый сигнал')
        try:
            signal=parse_signal(text)
        except ValueError:
            return dict(ok=False,reason='Не удалось определить токен и сеть')
        if signal['chain_id'] not in NATIVE:
            return dict(ok=False,reason='Для этой сети ещё нет парного DEX-контура')
        async with self.lock:
            async with aiosqlite.connect(self.path) as db:
                await db.execute('CREATE TABLE IF NOT EXISTS paired_channel_intake(message_id INTEGER PRIMARY KEY,ts REAL,status TEXT,payload TEXT)')
                old=await (await db.execute('SELECT payload FROM paired_channel_intake WHERE message_id=?',(post['id'],))).fetchone()
                if old and json.loads(old[0]).get('ok'):
                    return dict(json.loads(old[0]),duplicate=True)
            try:
                # Check Bybit directly: availability on MEXC is irrelevant.
                await asyncio.wait_for(self.discovery.ensure_evm('bybit',signal),20)
                if not 0<=self.clock()-post['timestamp']<=90:
                    raise ValueError('Сигнал устарел за время подбора')
                legacy=self.discovery.engines['dex_bybit'].source.routes
                found=[r for r in legacy if r['cex_venue']=='bybit' and r['base']==signal['base']
                       and r['chain_id']==signal['chain_id'] and r['asset'].lower()==signal['asset'].lower()]
                if len(found)!=1:raise ValueError('Нет однозначного маршрута Bybit ↔ DEX')
                route=dict(found[0],mode='paired',notional_usd=100,max_notional_usd=110,
                           native_symbol=NATIVE[signal['chain_id']]+'/USDT:USDT',native_decimals=18)
                routes_checked([route],300)
                routes=self.service.paper.source.routes
                existing=next((r for r in routes if route_key(r)==route_key(route)),None)
                if existing is None:
                    if len(routes)>=32:raise ValueError('Список маршрутов заполнен')
                    if any(r['base']==route['base'] and r['chain_id']==route['chain_id']
                           and r['asset'].lower()!=route['asset'].lower() for r in routes):
                        raise ValueError('Конфликт контрактов токена')
                    routes.append(route)
                self.service.prioritize(existing or route)
                result=dict(ok=True,reason='Токен поставлен в приоритет проверки',base=signal['base'])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                result=dict(ok=False,reason=str(exc) if isinstance(exc,ValueError) else 'Не удалось получить данные маршрута')
            result['elapsed_ms']=round((time.perf_counter()-started)*1000)
            async with aiosqlite.connect(self.path) as db:
                await db.execute('INSERT OR REPLACE INTO paired_channel_intake VALUES(?,?,?,?)',
                                 (post['id'],self.clock(),'READY' if result['ok'] else 'SKIP',json.dumps(result)))
                await db.commit()
            return result
