from .dex_quote_normalizer import normalize
class Service:
 def __init__(self,provider):self.provider=provider
 async def quote(self,chain,token_in,token_out,amount):
  x=await self.provider.quote(chain,token_in,token_out,amount)
  if not x.get("ok"):return x
  return normalize(x)
