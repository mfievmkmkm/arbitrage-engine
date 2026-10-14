from dataclasses import dataclass
@dataclass(frozen=True)
class Stage5:
 ready:bool;checks:dict
def assess(paper_trades,replay_valid,stage4_passed,private_ready=False):
 checks={"stage4":bool(stage4_passed),"paper_100":paper_trades>=100,"oos_replay":bool(replay_valid),"private_ready":bool(private_ready)}
 return Stage5(all(checks.values()),checks)
def render(x):
 return ("✅ ЭТАП 5: MICRO-LIVE READY" if x.ready else "🧪 ЭТАП 5: ПОДГОТОВКА")+"\n"+"\n".join(("✅ " if v else "⏳ ")+k for k,v in x.checks.items())
