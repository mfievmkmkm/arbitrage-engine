from dataclasses import dataclass
@dataclass(frozen=True)
class Gate:
 allowed:bool;reasons:tuple

def check(base_inventory_buy,base_inventory_sell,quote_buy,required_quote,required_base,transfer_needed=False):
 reasons=[]
 if quote_buy<required_quote:reasons.append("BUY_QUOTE_LOW")
 if base_inventory_sell<required_base:reasons.append("SELL_BASE_LOW")
 if transfer_needed:reasons.append("TRANSFER_DEPENDENCY")
 return Gate(not reasons,tuple(reasons))
