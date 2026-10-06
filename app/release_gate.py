from dataclasses import dataclass
@dataclass(frozen=True)
class ReleaseGate:
 discovery:bool;paper:bool;micro_live:bool;reasons:list
def evaluate(ci_green,scanner_ready,db_ready,e2e_green,paper_samples=0,replay_valid=False,private_streams=False,write_executor=False):
 reasons=[]
 discovery=ci_green and scanner_ready and db_ready
 paper=discovery and e2e_green
 micro=paper and paper_samples>=100 and replay_valid and private_streams and write_executor
 if not ci_green:reasons.append("CI_NOT_GREEN")
 if not e2e_green:reasons.append("E2E_NOT_GREEN")
 if paper_samples<100:reasons.append("PAPER_SAMPLE_LT_100")
 if not replay_valid:reasons.append("REPLAY_NOT_VALIDATED")
 if not private_streams:reasons.append("PRIVATE_STREAMS_MISSING")
 if not write_executor:reasons.append("WRITE_EXECUTOR_MISSING")
 return ReleaseGate(discovery,paper,micro,reasons)
