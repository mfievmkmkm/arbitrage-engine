import time
async def prune(diary,observation_days=30,execution_days=180):
 now=time.time();obs_cut=now-observation_days*86400;exe_cut=now-execution_days*86400
 import aiosqlite
 async with aiosqlite.connect(diary.path) as d:
  a=await d.execute("DELETE FROM observations WHERE ts<?",(obs_cut,))
  b=await d.execute("DELETE FROM execution_events WHERE ts<?",(exe_cut,))
  await d.commit()
  return {"observations":a.rowcount,"execution_events":b.rowcount}
