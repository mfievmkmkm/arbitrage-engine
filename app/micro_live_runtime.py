from dataclasses import dataclass
from .live_coordinator import LiveCoordinator
from .live_integrity import check as integrity
from .live_market_evidence import validate as market_validate
from .post_fill_guard import check as post_fill
from .live_incident_policy import classify

@dataclass(frozen=True)
class EntryRuntimeDecision:
 allowed:bool
 phase:str
 reasons:tuple

class MicroLiveRuntime:
 def __init__(self,coordinator):
  self.coordinator=coordinator
 def before_entry(self,symbol,long_venue,short_venue,open_trades,realized_net,no_withdraw,long_caps,short_caps,market_args,intent_states=(),trades=(),snapshot=None):
  c=self.coordinator.admission(symbol,long_venue,short_venue,open_trades,realized_net,no_withdraw,long_caps,short_caps)
  if not c.allowed:return EntryRuntimeDecision(False,"BLOCKED",(c.reason,))
  i=integrity(trades,snapshot or {},dict(intent_states))
  if not i.safe:return EntryRuntimeDecision(False,"BLOCKED",(i.reason,))
  m=market_validate(**market_args)
  if not m.allowed:return EntryRuntimeDecision(False,"BLOCKED",m.reasons)
  return EntryRuntimeDecision(True,"READY_TO_SUBMIT",())
 def after_fill(self,actual,min_net_edge_usd,safety_buffer=0):
  x=post_fill(actual,actual.base_qty,min_net_edge_usd,safety_buffer)
  return EntryRuntimeDecision(x.safe,"HEDGED" if x.safe else "PROTECTIVE_FLATTEN",(x.reason,))
 def on_failure(self,reason):
  p=classify(reason)
  if p.halt:self.coordinator.supervisor.kill.trip_global(reason)
  else:self.coordinator.incident(reason)
  return p
