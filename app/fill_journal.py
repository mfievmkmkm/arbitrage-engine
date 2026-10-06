async def entry(journal,trade_id,symbol,long_venue,short_venue,result):
 await journal.event(trade_id,"ENTRY_FILL",venue=long_venue,symbol=symbol,side="buy",qty=result.long_result.filled,price=result.long_result.avg_price,fee=result.long_result.fee)
 await journal.event(trade_id,"ENTRY_FILL",venue=short_venue,symbol=symbol,side="sell",qty=result.short_result.filled,price=result.short_result.avg_price,fee=result.short_result.fee)
async def exit(journal,trade,s):
 await journal.event(trade.trade_id,"EXIT_FILL",venue=trade.long_venue,symbol=trade.symbol,side="sell",qty=s.long_result.filled,price=s.long_result.avg_price,fee=s.long_result.fee)
 await journal.event(trade.trade_id,"EXIT_FILL",venue=trade.short_venue,symbol=trade.symbol,side="buy",qty=s.short_result.filled,price=s.short_result.avg_price,fee=s.short_result.fee)
