from .live_order_intent import OrderIntent
def entry(trade_id,venue,symbol,side,qty):return OrderIntent(f"{trade_id}:entry-recovery:{venue}:{side}",trade_id,venue,symbol,side,qty,False)
def close(trade_id,venue,symbol,side,qty):return OrderIntent(f"{trade_id}:close-recovery:{venue}:{side}",trade_id,venue,symbol,side,qty,True)
