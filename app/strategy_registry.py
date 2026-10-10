from dataclasses import dataclass
@dataclass(frozen=True)
class StrategyState:
 name:str;scan:bool;paper:bool;live:bool

def defaults():
 return {"futures_futures":StrategyState("futures_futures",True,True,False),"spot_futures":StrategyState("spot_futures",True,True,False),"spot_spot":StrategyState("spot_spot",True,True,False),"funding_arb":StrategyState("funding_arb",True,True,False),"cex_dex":StrategyState("cex_dex",False,False,False)}
