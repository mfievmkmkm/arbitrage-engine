from dataclasses import dataclass
from .book_freshness import check as freshness
from .live_funding_gate import check as funding
from .slippage_guard import check as slippage

@dataclass(frozen=True)
class MarketEvidence:
 allowed:bool
 reasons:tuple
def validate(now_ts,long_book_ts,short_book_ts,funding_known,funding_usd,max_funding_cost,long_ref,short_ref,long_fill,short_fill,max_slippage_pct):
 reasons=[]
 for x in (freshness(now_ts,long_book_ts),freshness(now_ts,short_book_ts)):
  if not x.ok:reasons.append(x.reason)
 fg=funding(funding_known,funding_usd,max_funding_cost)
 if not fg.allowed:reasons.append(fg.reason)
 if long_fill is not None:
  x=slippage(long_ref,long_fill,"buy",max_slippage_pct)
  if not x.allowed:reasons.append(x.reason+"_LONG")
 if short_fill is not None:
  x=slippage(short_ref,short_fill,"sell",max_slippage_pct)
  if not x.allowed:reasons.append(x.reason+"_SHORT")
 return MarketEvidence(not reasons,tuple(dict.fromkeys(reasons)))
