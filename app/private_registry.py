import asyncio
from .account_health import probe
class PrivateRegistry:
 def __init__(self):self.readers={}
 def add(self,name,reader):self.readers[name]=reader
 async def snapshot(self):
  names=list(self.readers)
  rows=await asyncio.gather(*(probe(self.readers[n]) for n in names),return_exceptions=True)
  out={}
  for name,row in zip(names,rows):
   if isinstance(row,Exception):
    out[name]={"health":type("Health",(),{"ok":False,"error":type(row).__name__})(),"positions":[],"orders":[],"balance":None}
   else:
    health,positions,orders,balance=row;out[name]={"health":health,"positions":positions,"orders":orders,"balance":balance}
  return out
 @property
 def configured(self):return bool(self.readers)
