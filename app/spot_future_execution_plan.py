from dataclasses import dataclass
@dataclass(frozen=True)
class Leg:
 market_type:str;symbol:str;side:str;base_qty:float;reduce_only:bool=False
@dataclass(frozen=True)
class Plan:
 spot:Leg;future:Leg

def build(op):
 q=op["base_qty"]
 if op["direction"]=="LONG_SPOT_SHORT_FUTURE":return Plan(Leg("spot",op["spot_symbol"],"buy",q),Leg("future",op["future_symbol"],"sell",q))
 return Plan(Leg("spot",op["spot_symbol"],"sell",q),Leg("future",op["future_symbol"],"buy",q))
