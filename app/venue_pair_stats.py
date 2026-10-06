from dataclasses import dataclass
@dataclass
class PairStats:
 opportunities:int=0;trades:int=0;wins:int=0;net:float=0;incidents:int=0
 def record(self,net=None,incident=False):
  self.opportunities+=1
  if net is not None:self.trades+=1;self.net+=net;self.wins+=int(net>0)
  self.incidents+=int(incident)
