from .account_health import probe
class PrivateRegistry:
 def __init__(self):self.readers={}
 def add(self,name,reader):self.readers[name]=reader
 async def snapshot(self):
  out={}
  for name,reader in self.readers.items():
   health,positions,orders,balance=await probe(reader)
   out[name]={"health":health,"positions":positions,"orders":orders,"balance":balance}
  return out
 @property
 def configured(self):return bool(self.readers)
