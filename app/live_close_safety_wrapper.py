from .live_phases import EXIT_SUBMITTING,CLOSED_PRIVATE_VERIFIED
async def before_close(store,trade,reason):await store.phase(trade.trade_id,EXIT_SUBMITTING,symbol=trade.symbol,long_venue=trade.long_venue,short_venue=trade.short_venue,reason=reason)
async def after_private_flat(store,trade,result):await store.phase(trade.trade_id,CLOSED_PRIVATE_VERIFIED,symbol=trade.symbol,long_venue=trade.long_venue,short_venue=trade.short_venue,pnl=getattr(result,"net_usd",None))
