from dataclasses import dataclass
@dataclass(frozen=True)
class NetworkGate:
 allowed:bool;reasons:tuple

def check(chain_ok,deposit_ok,withdraw_ok,gas_balance_ok):
 reasons=[]
 for ok,r in ((chain_ok,"CHAIN_UNAVAILABLE"),(deposit_ok,"DEPOSIT_DISABLED"),(withdraw_ok,"WITHDRAW_DISABLED"),(gas_balance_ok,"GAS_BALANCE_LOW")):
  if not ok:reasons.append(r)
 return NetworkGate(not reasons,tuple(reasons))
