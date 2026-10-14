from dataclasses import dataclass
@dataclass(frozen=True)
class Campaign:
 target_trades:int;closed:int;remaining:int;net:float;win_rate:float;status:str
async def status(diary,target=100):
 trades,net,wins=await diary.paper_stats();remaining=max(0,target-trades);wr=0 if not trades else wins/trades*100
 return Campaign(target,trades,remaining,net,wr,'READY_FOR_REPLAY' if remaining==0 else 'COLLECTING')
def render(c):
 return f'🧪 PAPER CAMPAIGN\nЗакрыто: {c.closed}/{c.target_trades}\nОсталось: {c.remaining}\nNET: ${c.net:+.4f}\nWin rate: {c.win_rate:.1f}%\nСтатус: {c.status}'
