from .exchange_executor import ExchangeExecutor,SubmitResult
class MockExecutor(ExchangeExecutor):
 def __init__(self,fill_ratio=1.0,price=100.0):
  self.fill_ratio=fill_ratio;self.price=price;self.orders={};self.seq=0
 async def submit(self,r):
  self.seq+=1;oid=str(self.seq);filled=r.qty*self.fill_ratio
  status="FILLED" if filled>=r.qty else "PARTIAL"
  x=SubmitResult(oid,status,filled,self.price);self.orders[oid]=x;return x
 async def cancel(self,order_id,symbol):
  x=self.orders[order_id];y=SubmitResult(order_id,"canceled",x.filled,x.avg_price);self.orders[order_id]=y;return y
 async def order(self,order_id,symbol):return self.orders[order_id]
