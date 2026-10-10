from dataclasses import replace

def merge_result(original,recovery):
 oq=float(original.filled);rq=float(recovery.filled)
 total=oq+rq
 if total<=0:return replace(original,filled=0,avg_price=None,fee=original.fee+recovery.fee)
 if oq>0 and original.avg_price is None:raise RuntimeError("MISSING_ORIGINAL_FILL_PRICE")
 if rq>0 and recovery.avg_price is None:raise RuntimeError("MISSING_RECOVERY_FILL_PRICE")
 price=((original.avg_price or 0)*oq+(recovery.avg_price or 0)*rq)/total
 return replace(original,filled=total,avg_price=price,fee=original.fee+recovery.fee)
