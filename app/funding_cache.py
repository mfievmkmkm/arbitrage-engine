import time
class FundingCache:
    def __init__(self,ttl=60):self.ttl=ttl;self.data={}
    def put(self,exchange,symbol,rate,next_ts=None,now=None):
        self.data[(exchange,symbol)]=(now or time.time(),rate,next_ts)
    def get(self,exchange,symbol,now=None):
        row=self.data.get((exchange,symbol))
        if not row:return None
        ts,rate,next_ts=row;now=now or time.time()
        if now-ts>self.ttl:return None
        return {"rate":rate,"next_ts":next_ts,"age":now-ts}
