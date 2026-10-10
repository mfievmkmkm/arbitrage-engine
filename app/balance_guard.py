from dataclasses import dataclass
@dataclass(frozen=True)
class BalanceDecision:
 allowed:bool
 required:float
 available:float
 reason:str
def check(available,notional,reserve_pct=20.0):
 required=notional*(1+reserve_pct/100)
 if available<required:return BalanceDecision(False,required,available,"INSUFFICIENT_MARGIN_BUFFER")
 return BalanceDecision(True,required,available,"OK")
