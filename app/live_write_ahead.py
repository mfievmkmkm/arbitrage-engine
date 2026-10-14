from .live_phases import PLANNED,ENTRY_SUBMITTING,HEDGED_PRIVATE_VERIFIED,OPEN,EXIT_SUBMITTING,CLOSED_PRIVATE_VERIFIED
class Journal:
 def __init__(self,store):self.store=store
 async def planned(self,id,**x):await self.store.phase(id,PLANNED,**x)
 async def entry_submitting(self,id,**x):await self.store.phase(id,ENTRY_SUBMITTING,**x)
 async def hedged(self,id,**x):await self.store.phase(id,HEDGED_PRIVATE_VERIFIED,**x)
 async def opened(self,id,**x):await self.store.phase(id,OPEN,**x)
 async def exit_submitting(self,id,**x):await self.store.phase(id,EXIT_SUBMITTING,**x)
 async def closed(self,id,**x):await self.store.phase(id,CLOSED_PRIVATE_VERIFIED,**x)
