from dataclasses import dataclass
from .private_residual import verify
@dataclass(frozen=True)
class CloseConfirmation:
 closed:bool;reason:str
def confirm(exit_result,snapshot,symbol,long_venue,short_venue):
 if not exit_result.flat:return CloseConfirmation(False,"EXECUTION_NOT_FLAT")
 r=verify(snapshot,symbol,long_venue,short_venue)
 return CloseConfirmation(r.flat,"CLOSED" if r.flat else r.reason)
