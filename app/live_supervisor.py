from dataclasses import dataclass
from .kill_switch import KillSwitch
from .error_circuit import ErrorCircuit
from .micro_live_evidence import evaluate as evidence

@dataclass
class LiveSupervisor:
 kill:object
 circuit:object
 ci_green:bool=False
 fee_verified:bool=False
 funding_known:bool=False
 book_fresh:bool=False
 private_verified:bool=False
 restart_clean:bool=False
 closed_e2e:bool=False
 unknown_orders:bool=True
 def __init__(self,error_threshold=3):
  self.kill=KillSwitch();self.circuit=ErrorCircuit(error_threshold)
  self.ci_green=self.fee_verified=self.funding_known=self.book_fresh=False
  self.private_verified=self.restart_clean=self.closed_e2e=False;self.unknown_orders=True
 def failure(self,reason):
  if self.circuit.failure(reason):self.kill.trip_global("ERROR_CIRCUIT:"+reason)
 def success(self):self.circuit.success()
 def readiness(self):
  k=not self.kill.check("", "", "").blocked
  return evidence(self.ci_green,self.private_verified,self.restart_clean,self.fee_verified,self.funding_known,self.book_fresh,k,self.unknown_orders,self.closed_e2e)
