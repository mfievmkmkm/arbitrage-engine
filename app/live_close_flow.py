import asyncio
from dataclasses import dataclass
from .close_flow import close_trade
from .live_close_gate import confirm

@dataclass(frozen=True)
class VerifiedClose:
 result:object
 execution:object
 status:str

async def _snapshot(source):
 value=source() if callable(source) else source
 if hasattr(value,"__await__"):value=await value
 return value

async def close_verified(t,long_executor,short_executor,private_snapshot,long_exit_price,short_exit_price,private_attempts=3,private_delay=.1,**kwargs):
 result,x,status=await close_trade(t,long_executor,short_executor,long_exit_price,short_exit_price,**kwargs)
 if status!="CLOSED":return VerifiedClose(result,x,status)
 last_reason="PRIVATE_STATE_UNTRUSTED"
 for attempt in range(max(1,private_attempts)):
  snapshot=await _snapshot(private_snapshot)
  c=confirm(x,snapshot,t.symbol,t.long_venue,t.short_venue)
  if c.closed:return VerifiedClose(result,x,"CLOSED")
  last_reason=c.reason
  if attempt+1<private_attempts and private_delay>0:await asyncio.sleep(private_delay)
 return VerifiedClose(result,x,"CLOSE_UNVERIFIED_"+last_reason)
