import asyncio
from types import SimpleNamespace as NS
from app.paired_signal_intake import Intake
from app.dex_virtual_paper_source import route_key
from app.secondary_strategy_runtime import SecondaryRuntime
from test_dex_virtual_paper import ROUTE, ASSET


class Discovery:
    def __init__(self):
        self.calls=[]
        self.engines={'dex_bybit':NS(source=NS(routes=[]))}
    async def ensure_evm(self,venue,signal):
        self.calls.append((venue,signal['asset']))
        self.engines['dex_bybit'].source.routes=[dict(ROUTE,mode='reference')]


def test_forwarded_identity_prioritizes_bybit_without_opening_and_deduplicates(tmp_path):
    async def run():
        discovery=Discovery();priorities=[]
        service=NS(paper=NS(source=NS(routes=[]),positions={}),prioritize=lambda r:priorities.append(route_key(r)))
        intake=Intake(tmp_path/'db',discovery,service,clock=lambda:1000)
        post=dict(id=42,timestamp=1000,text='SHORT? #X #ETHEREUM '+ASSET)
        result=await intake.accept(post)
        assert result['ok'] and discovery.calls==[('mexc',ASSET),('bybit',ASSET)]
        assert service.paper.source.routes[0]['mode']=='paired'
        assert priorities[0][3:] == ('bybit','X/USDT:USDT','paired')
        assert service.paper.positions=={}
        assert (await Intake(tmp_path/'db',discovery,service,clock=lambda:1000).accept(post))['duplicate']
        assert len(discovery.calls)==2 and len(priorities)==1
    asyncio.run(run())


def test_stale_reports_and_unsupported_networks_do_not_resolve(tmp_path):
    async def run():
        discovery=Discovery()
        intake=Intake(tmp_path/'db',discovery,object(),clock=lambda:1000)
        for stamp,text in ((900,'SHORT? #X #ETHEREUM '+ASSET),
                           (1000,'SHORT? #X #ETHEREUM '+ASSET+' Aligned in 20 sec'),
                           (1000,'SHORT? #SOL #SOLANA So11111111111111111111111111111111111111112')):
            assert not (await intake.accept(dict(id=1,timestamp=stamp,text=text)))['ok']
        assert discovery.calls==[]
    asyncio.run(run())


def test_failed_metadata_can_retry_and_conflicting_contract_cannot_enter(tmp_path):
    async def run():
        discovery=Discovery();priorities=[]
        service=NS(paper=NS(source=NS(routes=[dict(ROUTE,asset='0x'+'3'*40,mode='paired')])),prioritize=priorities.append)
        intake=Intake(tmp_path/'db',discovery,service,clock=lambda:1000)
        post=dict(id=4,timestamp=1000,text='SHORT? #X #ETHEREUM '+ASSET)
        assert not (await intake.accept(post))['ok']
        assert not priorities
        service.paper.source.routes=[]
        assert (await intake.accept(post))['ok']
    asyncio.run(run())


def test_event_wakes_scheduler_without_waiting_for_normal_interval():
    async def run():
        rt=SecondaryRuntime(None,None,None)
        service=NS(interval=60,wake=asyncio.Event())
        task=asyncio.create_task(rt.wait(service))
        service.wake.set()
        await asyncio.wait_for(task,.2)
        assert not service.wake.is_set()
    asyncio.run(run())
