from dataclasses import dataclass
@dataclass(frozen=True)
class ActualEntry:
 long_price:float;short_price:float;long_fee:float;short_fee:float;base_qty:float;spread_pct:float
def build(result,plan):
 lp=result.long_result.avg_price;sp=result.short_result.avg_price
 if lp is None or sp is None:raise RuntimeError("MISSING_ACTUAL_FILL_PRICE")
 lb=result.long_result.filled*plan.long.contract_size;sb=result.short_result.filled*plan.short.contract_size
 q=min(lb,sb)
 if q<=0:raise RuntimeError("NO_HEDGED_FILL")
 mismatch=abs(lb-sb)/max(lb,sb)
 if mismatch>.001:raise RuntimeError("ACTUAL_FILL_MISMATCH")
 return ActualEntry(float(lp),float(sp),float(result.long_result.fee),float(result.short_result.fee),q,(float(sp)-float(lp))/float(lp)*100)
