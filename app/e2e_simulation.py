from .runtime_engine import RuntimeEngine
from .runtime_store import RuntimeStore
from .trade_orchestrator import TradeOrchestrator
from .mock_executor import MockExecutor
from .position_manager import PositionManager
from .close_flow import close_trade
async def run(path,op,long_spec,short_spec):
 le=MockExecutor(price=op["entry_buy"]);se=MockExecutor(price=op["entry_sell"])
 runtime=RuntimeEngine(TradeOrchestrator(le,se),RuntimeStore(path),min_edge=0,bankroll=50)
 trade,status=await runtime.consider(op,long_spec,short_spec,round,round,op["entry_buy"],op["entry_sell"])
 if not trade:return {"status":status}
 pm=PositionManager(target_capture=.5)
 mark=pm.mark(trade,op["exit_buy"],op["exit_sell"],now=trade.opened_at+1)
 exit_le=MockExecutor(price=op["exit_buy"]);exit_se=MockExecutor(price=op["exit_sell"])
 result,exit_result,close_status=await close_trade(trade,exit_le,exit_se,op["exit_buy"],op["exit_sell"],reason=mark.decision.reason)
 if close_status=="CLOSED":runtime.remove(trade.trade_id)
 return {"status":close_status,"trade":trade,"mark":mark,"result":result,"remaining":len(runtime.trades)}
