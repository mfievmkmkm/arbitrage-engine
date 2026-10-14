from dataclasses import dataclass
@dataclass(frozen=True)
class Net:net_pct:float;cost_pct:float

def calculate(entry_executable_pct,entry_fees_pct,exit_fees_pct,funding_pct,entry_slippage_pct,exit_slippage_pct,safety_pct):
 cost=sum(map(float,(entry_fees_pct,exit_fees_pct,entry_slippage_pct,exit_slippage_pct,safety_pct)));return Net(float(entry_executable_pct)+float(funding_pct)-cost,cost)
