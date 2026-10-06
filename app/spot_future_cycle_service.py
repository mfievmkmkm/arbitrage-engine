class CycleService:
 def __init__(self,service,paper=None,entry_edge=1):self.service=service;self.paper=paper;self.entry_edge=entry_edge
 async def cycle(self):
  rows,_=await self.service.cycle()
  if self.paper:self.paper.spot_future(rows,self.entry_edge)
  return rows
