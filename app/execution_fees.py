from dataclasses import dataclass
from .fee_schedule import DEFAULT_FUTURES_FEES

@dataclass(frozen=True)
class ExecutionFees:
 long_rate:float
 short_rate:float
 long_liquidity:str
 short_liquidity:str

def liquidity(order_type,ioc=False):
 t=str(order_type or "").lower()
 if t=="market" or ioc:return "taker"
 if t in {"limit","post_only"}:return "maker"
 return "taker"

def resolve(long_venue,short_venue,long_request,short_request,schedule=DEFAULT_FUTURES_FEES):
 ll=liquidity(long_request.order_type,long_request.ioc)
 sl=liquidity(short_request.order_type,short_request.ioc)
 return ExecutionFees(schedule.require(long_venue,ll),schedule.require(short_venue,sl),ll,sl)
