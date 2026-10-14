from dataclasses import dataclass
@dataclass(frozen=True)
class PaperQuality:
 sample:int;wins:int;win_rate:float;net:float;ready_for_replay:bool
async def assess(diary,min_sample=100):
 trades,net,wins=await diary.paper_stats();wr=0 if not trades else wins/trades*100
 return PaperQuality(trades,wins,wr,net,trades>=min_sample)
def render(q):
 gate='OK' if q.ready_for_replay else 'НАКОПЛЕНИЕ'
 return f'Paper evidence: {q.sample} сделок • WR {q.win_rate:.1f}% • NET ${q.net:+.4f} • Replay gate: {gate}'
