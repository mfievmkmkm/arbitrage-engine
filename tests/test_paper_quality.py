import asyncio
from app.paper_quality import assess
class D:
 async def paper_stats(self):return (99,1,60)
def test_quality_gate():
 assert not asyncio.run(assess(D())).ready_for_replay
