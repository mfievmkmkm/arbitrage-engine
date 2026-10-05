class RotatingUniverse:
 def __init__(self,symbols_by_exchange,size=120,batch=30):
  counts={}
  for symbols in symbols_by_exchange.values():
   for symbol in symbols: counts[symbol]=counts.get(symbol,0)+1
  self.symbols=sorted((s for s,n in counts.items() if n>=2),key=lambda s:(-counts[s],s))[:size]
  self.batch_size=batch
  self.cursor=0
 def next(self):
  if not self.symbols:return []
  n=min(self.batch_size,len(self.symbols))
  out=[self.symbols[(self.cursor+i)%len(self.symbols)] for i in range(n)]
  self.cursor=(self.cursor+n)%len(self.symbols)
  return out
 @property
 def coverage(self):return len(self.symbols)
