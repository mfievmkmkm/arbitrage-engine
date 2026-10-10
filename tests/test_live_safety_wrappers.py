import asyncio
from app.live_entry_safety_wrapper import before_submit,after_private_verified
from app.live_close_safety_wrapper import before_close,after_private_flat
class S:
 def __init__(self):self.x=[]
 async def phase(self,i,p,**k):self.x.append(p)
def test_wrappers_write_durable_phases():
 async def go():
  s=S();await before_submit(s,"x",symbol="X");await after_private_verified(s,"x");assert s.x==["PLANNED","ENTRY_SUBMITTING","HEDGED_PRIVATE_VERIFIED"]
 asyncio.run(go())
