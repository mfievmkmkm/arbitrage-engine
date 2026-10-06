from dataclasses import dataclass
@dataclass(frozen=True)
class Acceptance:
 passed:bool
 checks:tuple
 failed:tuple
def evaluate(results):
 required=("ci","compile","unit","entry_e2e","exit_e2e","restart_e2e","unknown_order","private_reconcile","kill_switch","daily_stop","fee_verified","funding_known","withdraw_safe","venue_capabilities")
 failed=tuple(x for x in required if not results.get(x,False))
 return Acceptance(not failed,required,failed)
