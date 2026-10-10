from dataclasses import dataclass
from .entry_gate import check as entry_check
from .portfolio_limits import check as portfolio_check
@dataclass(frozen=True)
class OpportunityDecision:
 allowed:bool;reason:str
def check(op,min_edge,risk_ok,books_fresh,private_required,private_ready,bankroll,open_notional,open_trades,max_trades=2,max_utilization_pct=50):
 e=entry_check(op.get("hypothetical_edge",0),min_edge,risk_ok,books_fresh,op.get("funding_known",False),private_required,private_ready)
 if not e.allowed:return OpportunityDecision(False,e.reason)
 p=portfolio_check(bankroll,open_notional,op.get("notional",0),open_trades,max_trades,max_utilization_pct)
 if not p.allowed:return OpportunityDecision(False,p.reason)
 return OpportunityDecision(True,"OK")
