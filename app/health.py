import time
class VenueHealth:
    def __init__(self): self.data={}
    def _row(self,name): return self.data.setdefault(name,{"ok":0,"fail":0,"latency_ms":None,"last_ok":None,"last_error":None})
    def success(self,name,latency_ms):
        r=self._row(name);r["ok"]+=1;r["latency_ms"]=round(latency_ms,1);r["last_ok"]=time.time()
    def failure(self,name,error):
        r=self._row(name);r["fail"]+=1;r["last_error"]=str(error)[:80]
    def score(self,name):
        r=self._row(name);total=r["ok"]+r["fail"];return 100.0 if not total else round(100*r["ok"]/total,1)
    def snapshot(self): return {k:{**v,"success_pct":self.score(k)} for k,v in self.data.items()}
