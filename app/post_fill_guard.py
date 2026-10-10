from dataclasses import dataclass
@dataclass(frozen=True)
class PostFillDecision:
 safe:bool
 action:str
 actual_net_edge:float
 reason:str

def check(actual,base_qty,min_net_edge_usd,safety_buffer=0):
 gross=(actual.short_price-actual.long_price)*base_qty
 fees=actual.long_fee+actual.short_fee
 net=gross-fees-max(0,float(safety_buffer))
 if net>=min_net_edge_usd:return PostFillDecision(True,"KEEP",net,"ACTUAL_NET_OK")
 return PostFillDecision(False,"FLATTEN",net,"ACTUAL_NET_DETERIORATED")
