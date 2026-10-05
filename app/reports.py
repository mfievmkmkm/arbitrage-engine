import json
from .replay import portfolio_replay,ai_report,walk_forward
async def build_replay_report(diary):
 trades=await diary.replay_trades()
 rows=portfolio_replay(trades)
 report=ai_report(rows)
 report["walk_forward"]=walk_forward(trades)
 return rows,report
def compact_ai_json(report):
 return json.dumps(report,ensure_ascii=False,separators=(",",":"))
