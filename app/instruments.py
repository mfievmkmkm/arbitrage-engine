from dataclasses import dataclass
@dataclass(frozen=True)
class InstrumentSpec:
    exchange:str;symbol:str;base:str;quote:str;settle:str;contract:bool;linear:bool
    amount_min:float|None;cost_min:float|None;amount_precision:float|None;contract_size:float
def from_market(exchange,m):
    limits=m.get("limits") or {};amount=limits.get("amount") or {};cost=limits.get("cost") or {};precision=m.get("precision") or {}
    return InstrumentSpec(exchange,m["symbol"],m.get("base",""),m.get("quote",""),m.get("settle",""),bool(m.get("contract")),bool(m.get("linear")),amount.get("min"),cost.get("min"),precision.get("amount"),float(m.get("contractSize") or 1.0))
def compatible(a,b):
    return a.symbol==b.symbol and a.base==b.base and a.quote==b.quote and a.settle==b.settle=="USDT" and a.contract and b.contract and a.linear and b.linear
def min_notional_ok(spec,notional,price=None):
    if spec.cost_min is not None:return notional>=spec.cost_min
    if spec.amount_min is not None and price is not None:return notional>=spec.amount_min*spec.contract_size*price
    return True
