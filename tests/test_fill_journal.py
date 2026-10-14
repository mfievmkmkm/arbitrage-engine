import asyncio
from app.fill_journal import entry
from app.exchange_executor import SubmitResult
class J:
 def __init__(self):self.x=[]
 async def event(self,*a,**k):self.x.append((a,k))
class R:
 long_result=SubmitResult("1","FILLED",1,101,.1);short_result=SubmitResult("2","FILLED",1,109,.2)
def test_entry_journal():
 async def go():
  j=J();await entry(j,"t","X","a","b",R());assert len(j.x)==2 and j.x[0][1]["price"]==101
 asyncio.run(go())
