from .exit_runner import run as exit_run
from .trade_result import finalize
async def close_trade(t,long_executor,short_executor,long_exit_price,short_exit_price,exit_fees=0,funding=None,reason="EXIT"):
 x=await exit_run(t.symbol,t.long_contracts,t.short_contracts,t.long_contract_size,t.short_contract_size,long_executor,short_executor)
 if not x.flat:return None,x,"EXIT_PARTIAL_REQUIRES_RECOVERY"
 f=t.funding if funding is None else funding
 capital=t.base_qty*((t.long_entry+t.short_entry)/2)
 result=finalize(t.trade_id,t.base_qty,t.long_entry,t.short_entry,long_exit_price,short_exit_price,t.entry_fees,exit_fees,f,capital,reason)
 return result,x,"CLOSED"
