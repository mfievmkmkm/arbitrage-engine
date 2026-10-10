from dataclasses import dataclass
@dataclass(frozen=True)
class VenueSize:
 contracts:float;base_amount:float;valid:bool;reason:str
def size(client,spec,base_amount,price):
 raw=base_amount/spec.contract_size
 try:contracts=float(client.amount_to_precision(spec.symbol,raw))
 except Exception:contracts=raw
 if contracts<=0:return VenueSize(0,0,False,"ZERO_SIZE")
 base=contracts*spec.contract_size
 if spec.amount_min is not None and contracts<spec.amount_min:return VenueSize(contracts,base,False,"AMOUNT_MIN")
 if spec.cost_min is not None and base*price<spec.cost_min:return VenueSize(contracts,base,False,"COST_MIN")
 return VenueSize(contracts,base,True,"OK")
def matched(a,b,tolerance_pct=.1):
 scale=max(a.base_amount,b.base_amount)
 return a.valid and b.valid and scale>0 and abs(a.base_amount-b.base_amount)/scale*100<=tolerance_pct
