from dataclasses import dataclass
@dataclass(frozen=True)
class OrderPolicy:
 order_type:str
 ioc:bool
 max_slippage_pct:float
 reason:str
def choose(edge_pct,spread_pct,liquidity_ok,urgent=False):
 if not liquidity_ok:return OrderPolicy("none",False,0,"INSUFFICIENT_LIQUIDITY")
 if urgent:return OrderPolicy("market",False,min(.30,max(.05,edge_pct*.15)),"RECOVERY")
 if spread_pct<=.04:return OrderPolicy("limit",True,min(.15,max(.03,edge_pct*.10)),"TIGHT_BOOK_IOC")
 return OrderPolicy("limit",True,min(.20,max(.04,edge_pct*.12)),"PASSIVE_IOC")
