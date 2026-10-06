import asyncio
from dataclasses import dataclass
from .exchange_executor import SubmitRequest

@dataclass(frozen=True)
class PrivateRecovery:
 recovered:bool
 reason:str
 long_result:object=None
 short_result:object=None

def _contracts(snapshot,venue,symbol,side,tolerance=1e-12):
 row=snapshot.get(venue)
 if not row or not getattr(row.get("health"),"ok",False):return None
 total=0.0
 opposite=0.0
 for p in row.get("positions",[]):
  if p.symbol!=symbol:continue
  q=p.contracts
  if q is None:
   size=float(getattr(p,"contract_size",1) or 1)
   q=abs(float(p.qty))/size
  q=abs(float(q))
  if str(p.side).lower()==side:total+=q
  elif q>tolerance:opposite+=q
 return total,opposite

async def recover_from_private(trade,snapshot,long_executor,short_executor,timeout=8,tolerance=1e-12):
 a=_contracts(snapshot,trade.long_venue,trade.symbol,"long",tolerance)
 b=_contracts(snapshot,trade.short_venue,trade.symbol,"short",tolerance)
 if a is None or b is None:return PrivateRecovery(False,"PRIVATE_STATE_UNTRUSTED")
 if a[1]>tolerance or b[1]>tolerance:return PrivateRecovery(False,"OPPOSITE_OR_FLIPPED_EXPOSURE")
 l,s=a[0],b[0]
 if l<=tolerance and s<=tolerance:return PrivateRecovery(True,"ALREADY_FLAT")
 requests=[]
 if l>tolerance:requests.append(("long",long_executor,SubmitRequest(trade.symbol,"sell",l,"market",None,True,False)))
 if s>tolerance:requests.append(("short",short_executor,SubmitRequest(trade.symbol,"buy",s,"market",None,True,False)))
 async def submit(ex,r):
  try:return await asyncio.wait_for(ex.submit(r),timeout)
  except Exception as e:return e
 results=await asyncio.gather(*(submit(ex,r) for _,ex,r in requests))
 out={"long":None,"short":None}
 for (leg,_,req),r in zip(requests,results):
  if isinstance(r,Exception):return PrivateRecovery(False,"PRIVATE_RECOVERY_"+type(r).__name__,out["long"],out["short"])
  out[leg]=r
  if r.filled>req.qty+tolerance:return PrivateRecovery(False,"PRIVATE_RECOVERY_OVERFILL",out["long"],out["short"])
  if r.filled<req.qty-tolerance:return PrivateRecovery(False,"PRIVATE_RECOVERY_PARTIAL",out["long"],out["short"])
 return PrivateRecovery(True,"PRIVATE_RECOVERY_SUBMITTED",out["long"],out["short"])
