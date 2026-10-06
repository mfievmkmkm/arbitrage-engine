from dataclasses import dataclass,replace
from .fill_reconcile import reconcile

@dataclass(frozen=True)
class EffectiveEntry:
 result:object
 hedged:bool
 reason:str

def merge(plan,initial,recovery):
 if recovery is None or recovery.action=="HEDGED":
  return EffectiveEntry(initial,initial.hedged,"UNCHANGED")
 rr=recovery.result
 if rr is None or not recovery.completed:return EffectiveEntry(initial,False,"RECOVERY_INCOMPLETE")
 long_r=initial.long_result;short_r=initial.short_result
 if recovery.action=="COMPLETE":
  lb=long_r.filled*plan.long.contract_size
  sb=short_r.filled*plan.short.contract_size
  if lb<sb:
   total=long_r.filled+rr.filled
   price=rr.avg_price if long_r.filled<=1e-12 else ((long_r.avg_price or rr.avg_price)*long_r.filled+(rr.avg_price or long_r.avg_price)*rr.filled)/total
   long_r=replace(long_r,filled=total,avg_price=price,fee=long_r.fee+rr.fee)
  else:
   total=short_r.filled+rr.filled
   price=rr.avg_price if short_r.filled<=1e-12 else ((short_r.avg_price or rr.avg_price)*short_r.filled+(rr.avg_price or short_r.avg_price)*rr.filled)/total
   short_r=replace(short_r,filled=total,avg_price=price,fee=short_r.fee+rr.fee)
 elif recovery.action=="FLATTEN":
  return EffectiveEntry(initial,False,"ENTRY_FLATTENED")
 else:return EffectiveEntry(initial,False,"UNKNOWN_RECOVERY_ACTION")
 rec=reconcile(long_r.filled,plan.long.contract_size,short_r.filled,plan.short.contract_size)
 hedged=rec.hedged
 return EffectiveEntry(replace(initial,long_result=long_r,short_result=short_r,hedged=hedged,mismatch_pct=rec.mismatch_pct),hedged,"RECOVERED" if hedged else "RECOVERY_MISMATCH")
