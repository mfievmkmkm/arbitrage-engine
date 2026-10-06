from dataclasses import dataclass
@dataclass(frozen=True)
class SymbolApproval:
 allowed:bool
 reason:str
def check(long_active,short_active,long_contract_size,short_contract_size):
 if not long_active or not short_active:return SymbolApproval(False,"SYMBOL_INACTIVE")
 if long_contract_size<=0 or short_contract_size<=0:return SymbolApproval(False,"CONTRACT_SIZE_INVALID")
 return SymbolApproval(True,"OK")
