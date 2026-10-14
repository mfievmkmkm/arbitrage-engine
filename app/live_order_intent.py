from dataclasses import dataclass,asdict
@dataclass(frozen=True)
class OrderIntent:
 intent_id:str;trade_id:str;venue:str;symbol:str;side:str;qty:float;reduce_only:bool;state:str="PLANNED"
 def row(self):return asdict(self)
TERMINAL={"FILLED","CANCELED","REJECTED","FAILED"}
def may_submit(intent,known_states):
 state=known_states.get(intent.intent_id)
 if state is None:return True,"NEW"
 if state in TERMINAL:return False,"TERMINAL"
 return False,"RECONCILE_REQUIRED"
