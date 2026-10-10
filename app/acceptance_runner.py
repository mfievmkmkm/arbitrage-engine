from dataclasses import dataclass
from .micro_live_acceptance import evaluate
@dataclass(frozen=True)
class AcceptanceReport:
 passed:bool
 results:dict
 failed:tuple
def run(ci_green,compile_ok,unit_ok,entry_e2e,exit_e2e,restart_e2e,unknown_order_safe,private_reconcile,kill_switch,daily_stop,fee_verified,funding_known,withdraw_safe,venue_capabilities):
 r={"ci":ci_green,"compile":compile_ok,"unit":unit_ok,"entry_e2e":entry_e2e,"exit_e2e":exit_e2e,"restart_e2e":restart_e2e,"unknown_order":unknown_order_safe,"private_reconcile":private_reconcile,"kill_switch":kill_switch,"daily_stop":daily_stop,"fee_verified":fee_verified,"funding_known":funding_known,"withdraw_safe":withdraw_safe,"venue_capabilities":venue_capabilities}
 a=evaluate(r);return AcceptanceReport(a.passed,r,a.failed)
def render(x):
 return ("🟢 MICRO-LIVE ACCEPTANCE: PASSED" if x.passed else "🔒 MICRO-LIVE ACCEPTANCE: LOCKED\nFailed: "+", ".join(x.failed))
