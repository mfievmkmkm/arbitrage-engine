from dataclasses import dataclass
STATES=("PLANNED","SUBMITTING","SUBMITTED","ACK","PARTIAL","FILLED","CANCELED","REJECTED","FAILED","UNKNOWN")
TERMINAL={"FILLED","CANCELED","REJECTED","FAILED"}
_ALLOWED={
 "PLANNED":{"SUBMITTING","CANCELED"},
 "SUBMITTING":{"SUBMITTED","ACK","PARTIAL","FILLED","REJECTED","FAILED","UNKNOWN"},
 "SUBMITTED":{"ACK","PARTIAL","FILLED","CANCELED","REJECTED","UNKNOWN"},
 "ACK":{"PARTIAL","FILLED","CANCELED","REJECTED","UNKNOWN"},
 "PARTIAL":{"PARTIAL","FILLED","CANCELED","UNKNOWN"},
 "UNKNOWN":{"ACK","PARTIAL","FILLED","CANCELED","REJECTED","FAILED"},
}
@dataclass(frozen=True)
class Transition:
 allowed:bool;old:str;new:str;reason:str
def transition(old,new):
 if old==new and old=="PARTIAL":return Transition(True,old,new,"UPDATE")
 if old in TERMINAL:return Transition(False,old,new,"TERMINAL")
 if new not in STATES:return Transition(False,old,new,"UNKNOWN_STATE")
 return Transition(new in _ALLOWED.get(old,set()),old,new,"OK" if new in _ALLOWED.get(old,set()) else "INVALID_TRANSITION")
def restart_action(state):
 if state in TERMINAL:return "NO_ACTION"
 if state=="PLANNED":return "SAFE_NOT_SUBMITTED"
 return "RECONCILE_EXCHANGE"
