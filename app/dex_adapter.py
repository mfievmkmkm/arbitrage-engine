from dataclasses import dataclass
@dataclass(frozen=True)
class QuoteRequest:
 chain:str;token_in:str;token_out:str;amount_in:float;wallet:str|None=None
class DexAdapter:
 async def quote(self,request):raise NotImplementedError
 async def token_info(self,chain,address):raise NotImplementedError
 async def network_state(self,chain):raise NotImplementedError
