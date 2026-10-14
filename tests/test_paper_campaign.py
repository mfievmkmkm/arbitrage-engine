import asyncio
from app.paper_campaign import status
class D:
 async def paper_stats(self):return 25,1.2,15
def test_campaign():assert asyncio.run(status(D())).remaining==75
