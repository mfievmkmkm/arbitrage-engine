from dataclasses import dataclass
from .exchange_executor import SubmitRequest
@dataclass(frozen=True)
class ExitPlan:
 long_close:SubmitRequest
 short_close:SubmitRequest
def build(symbol,long_contracts,short_contracts,order_type="market"):
 return ExitPlan(
  SubmitRequest(symbol,"sell",long_contracts,order_type,None,True,False),
  SubmitRequest(symbol,"buy",short_contracts,order_type,None,True,False))
