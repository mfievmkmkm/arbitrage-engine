import time
class MarketCache:
    def __init__(self,max_age=5.0):self.max_age=max_age;self.books={}
    def put(self,exchange,symbol,book,ts=None):self.books[(exchange,symbol)]=(ts or time.time(),book)
    def get(self,exchange,symbol,now=None):
        item=self.books.get((exchange,symbol))
        if not item:return None
        ts,book=item;now=now or time.time()
        return None if now-ts>self.max_age else book
    def purge(self,now=None):
        now=now or time.time()
        for k,(ts,_) in list(self.books.items()):
            if now-ts>self.max_age:self.books.pop(k,None)
