from dataclasses import dataclass
@dataclass(frozen=True)
class Acceptance:
 passed:bool;checks:dict
def assess(two_leg_safe,exit_safe,recovery_safe,restart_safe,diary_safe,live_locked,e2e_safe):
 checks={"two_leg":two_leg_safe,"exit":exit_safe,"recovery":recovery_safe,"restart":restart_safe,"diary":diary_safe,"live_lock":live_locked,"e2e":e2e_safe}
 return Acceptance(all(checks.values()),checks)
def render(a):
 head="✅ ЭТАП 4 ГОТОВ" if a.passed else "🟡 ЭТАП 4 НЕ ЗАКРЫТ"
 return head+"\n"+"\n".join(("✅ " if v else "❌ ")+k for k,v in a.checks.items())
