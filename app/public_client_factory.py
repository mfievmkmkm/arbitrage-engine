import ccxt.async_support as ccxt
async def build(ids,default_type=None):
 out={}
 for name in ids:
  try:
   opts={"enableRateLimit":True};
   if default_type:opts["options"]={"defaultType":default_type}
   c=getattr(ccxt,name)(opts);await c.load_markets();out[name]=c
  except Exception:
   try:await c.close()
   except Exception:pass
 return out
async def close(clients):
 import asyncio
 await asyncio.gather(*(c.close() for c in clients.values()),return_exceptions=True)
