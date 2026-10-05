import json,time
from dataclasses import dataclass,asdict
@dataclass(frozen=True)
class ExecutionEvent:
 trade_id:str
 kind:str
 venue:str=""
 symbol:str=""
 side:str=""
 qty:float=0.0
 price:float|None=None
 fee:float=0.0
 reason:str=""
 ts:float=0.0
 def row(self):
  d=asdict(self)
  if not d["ts"]:d["ts"]=time.time()
  return d
def encode(event):return json.dumps(event.row(),separators=(",",":"),sort_keys=True)
