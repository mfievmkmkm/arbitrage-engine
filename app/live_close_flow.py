from dataclasses import dataclass
from .close_flow import close_trade
from .live_close_gate import confirm
@dataclass(frozen=True)
class VerifiedClose:
 result:object;execution:object;status:str
async def close_verified(t,long_executor,short_executor,private_snapshot,long_exit_price,short_exit_price,**kwargs):
 result,x,status=await close_trade(t,long_executor,short_executor,long_exit_price,short_exit_price,**kwargs)
 if status!="CLOSED":return VerifiedClose(result,x,status)
 c=confirm(x,private_snapshot,t.symbol,t.long_venue,t.short_venue)
 if not c.closed:return VerifiedClose(result,x,"CLOSE_UNVERIFIED_"+c.reason)
 return VerifiedClose(result,x,"CLOSED")
