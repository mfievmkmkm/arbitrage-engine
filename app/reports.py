import json
from .replay import portfolio_replay,ai_report
async def build_replay_report(diary):
 trades=await diary.replay_trades()
 rows=portfolio_replay(trades)
 return rows,ai_report(rows)
def compact_ai_json(report):
 return json.dumps(report,ensure_ascii=False,separators=(",",":"))
