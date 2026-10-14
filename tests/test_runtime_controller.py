import asyncio
from app.runtime_controller import RuntimeController
from app.runtime_state import RuntimeTrade
from app.mock_executor import MockExecutor
class R:
 def __init__(self):self.removed=[]
 def remove(self,x):self.removed.append(x)
def test_controller_close():
 async def go():
  r=R();t=RuntimeTrade("1","X","a","b",1,1,1,1,1,100,110,0)
  c=RuntimeController(r,{"a":MockExecutor()},{"b":MockExecutor()})
  out,mark,status=await c.maybe_close(t,104,106,now=2000)
  assert status=="CLOSED" and r.removed==["1"]
 asyncio.run(go())
