from .live_phases import PLANNED,ENTRY_SUBMITTING,HEDGED_PRIVATE_VERIFIED
async def before_submit(store,trade_id,**meta):await store.phase(trade_id,PLANNED,**meta);await store.phase(trade_id,ENTRY_SUBMITTING,**meta)
async def after_private_verified(store,trade_id,**meta):await store.phase(trade_id,HEDGED_PRIVATE_VERIFIED,**meta)
