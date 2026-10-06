from dataclasses import dataclass
@dataclass(frozen=True)
class CloseRecovery:
 required:bool;venue:str|None;side:str|None;contracts:float;reason:str
def plan(trade,exit_result,tolerance=1e-12):
 lr=max(0,trade.long_contracts-exit_result.long_result.filled)
 sr=max(0,trade.short_contracts-exit_result.short_result.filled)
 if lr<=tolerance and sr<=tolerance:return CloseRecovery(False,None,None,0,"FLAT_BY_FILLS")
 if lr>tolerance and sr<=tolerance:return CloseRecovery(True,trade.long_venue,"sell",lr,"LONG_RESIDUAL")
 if sr>tolerance and lr<=tolerance:return CloseRecovery(True,trade.short_venue,"buy",sr,"SHORT_RESIDUAL")
 return CloseRecovery(True,None,None,0,"BOTH_LEGS_RESIDUAL_RECONCILE")
