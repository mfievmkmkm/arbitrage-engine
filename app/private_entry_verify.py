import asyncio
from dataclasses import dataclass

@dataclass(frozen=True)
class EntryPrivateCheck:
 verified:bool
 reason:str
 long_base:float=0
 short_base:float=0

def _side(snapshot,venue,symbol,expected):
 row=snapshot.get(venue)
 if not row or not getattr(row.get("health"),"ok",False):return None,"PRIVATE_STATE_UNTRUSTED"
 good=bad=0.0
 for p in row.get("positions",[]):
  if p.symbol!=symbol:continue
  q=abs(float(p.qty))
  if str(p.side).lower()==expected:good+=q
  elif q>1e-12:bad+=q
 if bad>1e-12:return None,"OPPOSITE_OR_FLIPPED_EXPOSURE"
 return good,"OK"

async def verify(snapshot_source,symbol,long_venue,short_venue,expected_base,attempts=3,delay=.25,tolerance_pct=.001):
 last="PRIVATE_STATE_UNTRUSTED"
 for i in range(max(1,attempts)):
  try:
   snap=snapshot_source()
   if hasattr(snap,"__await__"):snap=await snap
  except Exception:
   snap=None
  if snap:
   l,lr=_side(snap,long_venue,symbol,"long");s,sr=_side(snap,short_venue,symbol,"short")
   if l is not None and s is not None:
    scale=max(expected_base,l,s,1e-12)
    if abs(l-expected_base)<=scale*tolerance_pct and abs(s-expected_base)<=scale*tolerance_pct and abs(l-s)<=scale*tolerance_pct:
     return EntryPrivateCheck(True,"VERIFIED",l,s)
    last="PRIVATE_POSITION_MISMATCH"
   else:last=lr if l is None else sr
  if i+1<attempts:await asyncio.sleep(delay)
 return EntryPrivateCheck(False,last)
