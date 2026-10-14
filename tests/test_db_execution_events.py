import asyncio
from app.db import Diary
from app.execution_diary import ExecutionEvent
def test_execution_event_roundtrip(tmp_path):
 async def go():
  d=Diary(str(tmp_path/"x.db"));await d.init()
  await d.record_execution_event(ExecutionEvent("t","MARK",symbol="X",net=1.5,spread=.7))
  rows=await d.all_execution_events();assert rows[0]["net"]==1.5 and rows[0]["spread"]==.7
 asyncio.run(go())
