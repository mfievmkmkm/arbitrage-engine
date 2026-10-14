from dataclasses import dataclass
@dataclass(frozen=True)
class ExecutionHealth:
 safe:bool
 reasons:tuple
def evaluate(symbol_ok,margin_ok,liquidity_ok,funding_window_ok,pair_cooldown_ok,private_snapshot_fresh,drawdown_ok):
 reasons=[]
 for ok,r in ((symbol_ok,"SYMBOL"),(margin_ok,"MARGIN"),(liquidity_ok,"LIQUIDITY"),(funding_window_ok,"FUNDING_WINDOW"),(pair_cooldown_ok,"PAIR_COOLDOWN"),(private_snapshot_fresh,"PRIVATE_STALE"),(drawdown_ok,"DRAWDOWN")):
  if not ok:reasons.append(r)
 return ExecutionHealth(not reasons,tuple(reasons))
