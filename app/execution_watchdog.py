import time
from dataclasses import dataclass
@dataclass
class Watchdog:
 timeout:float
 started:float|None=None
 def start(self,now=None):self.started=time.monotonic() if now is None else now
 def expired(self,now=None):
  if self.started is None:return False
  now=time.monotonic() if now is None else now
  return now-self.started>=self.timeout
 def action(self,hedged):
  if not self.expired():return "WAIT"
  return "HOLD" if hedged else "RECOVER"
