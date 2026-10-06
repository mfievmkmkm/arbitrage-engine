from dataclasses import dataclass
@dataclass(frozen=True)
class Readiness:
 ready:bool;checks:dict
def assess(db_ok,scanner_ok,paper_ok,replay_ok,runtime_ok,private_reconciled=False,live_requested=False):
 checks={"db":db_ok,"scanner":scanner_ok,"paper":paper_ok,"replay":replay_ok,"runtime":runtime_ok}
 if live_requested:checks["private_reconciled"]=private_reconciled
 return Readiness(all(checks.values()),checks)
def render(r):
 return "\n".join(["✅ RELEASE CHECK" if r.ready else "⚠️ RELEASE CHECK"]+[f"{'OK' if v else 'FAIL'} • {k}" for k,v in r.checks.items()])
