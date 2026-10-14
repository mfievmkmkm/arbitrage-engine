import json
from app.execution_diary import ExecutionEvent,encode
def test_event():
 x=json.loads(encode(ExecutionEvent("t","FILL","a","X","buy",1,100,0.1)))
 assert x["kind"]=="FILL" and x["fee"]==.1 and x["ts"]>0
