import asyncio,time
class SecondaryRuntime:
 def __init__(self,runtime,diary_record,db_path,interval=30):self.runtime=runtime;self.record=diary_record;self.db_path=db_path;self.interval=interval;self.services={};self.tasks=[];self.running=False
 def add(self,name,service):self.services[name]=service
 async def _loop(self,name,service):
  while self.running:
   try:
    rows=await service.cycle();ts=time.time();self.runtime.update(name,rows);obs=[]
    for x in rows:
     y=dict(x) if isinstance(x,dict) else dict(x.__dict__);y.setdefault("strategy",name);y.setdefault("ts",ts);obs.append({"ts":y["ts"],"strategy":name,"symbol":y.get("symbol") or y.get("base",""),"venue":y.get("exchange") or y.get("buy") or y.get("long_venue",""),"edge":y.get("hypothetical_edge",y.get("net",y.get("carry_pct",0))),"notional":y.get("notional",0),"payload":y})
    await self.record(self.db_path,obs)
   except asyncio.CancelledError:raise
   except Exception:self.runtime.fail(name)
   await asyncio.sleep(self.interval)
 async def start(self):
  self.running=True;self.tasks=[asyncio.create_task(self._loop(n,s)) for n,s in self.services.items()]
 async def stop(self):
  self.running=False
  for t in self.tasks:t.cancel()
  await asyncio.gather(*self.tasks,return_exceptions=True);self.tasks=[]
