from dataclasses import dataclass
@dataclass(frozen=True)
class Allocation:
 allowed:bool;strategy:str;notional:float;reason:str

def allocate(strategy,requested,bankroll,live_enabled=False):
 caps={"futures_futures":.10,"spot_futures":.05,"cex_dex":0}
 if strategy not in caps:return Allocation(False,strategy,0,"UNKNOWN_STRATEGY")
 if live_enabled and strategy!="futures_futures":return Allocation(False,strategy,0,"STRATEGY_LIVE_LOCKED")
 cap=bankroll*caps[strategy]
 return Allocation(requested>0 and requested<=cap,strategy,min(max(requested,0),cap),"OK" if requested>0 and requested<=cap else "STRATEGY_BUDGET")
