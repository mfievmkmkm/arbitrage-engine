import asyncio
from app.stage5_e2e import run
from app.mock_executor import MockExecutor
class D:
 def __init__(self):self.s={}
 async def order_intent_states(self,*a):return self.s.copy()
 async def save_order_intent(self,i,state=None):self.s[i.intent_id]=state
def test_e2e():
 x=asyncio.run(run(D(),MockExecutor()));assert x["ok"] and x["state"]=="FILLED"
