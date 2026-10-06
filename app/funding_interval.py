from dataclasses import dataclass
@dataclass(frozen=True)
class FundingInterval:
 known:bool;hours:float|None;reason:str

def infer(row):
 info=row.get("info") or {};v=row.get("interval") or info.get("fundingIntervalHours") or info.get("fundingInterval")
 try:
  h=float(v)
  if h>1000:h/=3600000
  return FundingInterval(h>0,h if h>0 else None,"OK" if h>0 else "INVALID")
 except Exception:return FundingInterval(False,None,"UNKNOWN")
