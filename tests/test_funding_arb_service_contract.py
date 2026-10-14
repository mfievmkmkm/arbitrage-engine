import asyncio
from types import SimpleNamespace
from app.funding_arb_service import Service
class F:
 async def get(self,v,s):return SimpleNamespace(rate=-.001 if v=="a" else .002,interval_hours=8)
def test_service_uses_snapshot_rate_contract():
 x=asyncio.run(Service(F(),["a","b"],.05).scan("X"));assert x and x[0].long_venue=="a"
