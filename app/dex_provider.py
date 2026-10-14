class QuoteProvider:
 async def quote(self,chain,token_in,token_out,amount):raise NotImplementedError
class DisabledProvider(QuoteProvider):
 async def quote(self,*a,**k):return {"ok":False,"reason":"DEX_PROVIDER_DISABLED"}
