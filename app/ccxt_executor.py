import asyncio
from .exchange_executor import ExchangeExecutor,SubmitResult
from .order_status import normalize
class CCXTExecutor(ExchangeExecutor):
 def __init__(self,venue,client,timeout=8):
  self.venue=venue;self.client=client;self.timeout=timeout
 def _result(self,row):
  fee=row.get("fee") or {};fees=row.get("fees") or []
  fee_cost=float(fee.get("cost") or 0)
  if not fee_cost:fee_cost=sum(float(x.get("cost") or 0) for x in fees)
  filled=float(row.get("filled") or 0);amount=row.get("amount");status=normalize(row.get("status"),filled,float(amount) if amount is not None else None)
  return SubmitResult(str(row.get("id") or ""),status,filled,row.get("average") or row.get("price"),fee_cost)
 async def submit(self,r):
  params={}
  if r.reduce_only:params["reduceOnly"]=True
  if r.ioc:params["timeInForce"]="IOC"
  if r.client_order_id:params["clientOrderId"]=r.client_order_id
  row=await asyncio.wait_for(self.client.create_order(r.symbol,r.order_type,r.side,r.qty,r.price,params),self.timeout)
  return self._result(row)
 async def order_by_client_id(self,client_order_id,symbol):
  if not hasattr(self.client,"fetch_orders"):raise RuntimeError("CLIENT_ORDER_LOOKUP_UNSUPPORTED")
  rows=await asyncio.wait_for(self.client.fetch_orders(symbol),self.timeout)
  for row in rows:
   cid=str(row.get("clientOrderId") or row.get("clientOrderID") or "")
   if cid==client_order_id:return self._result(row)
  raise RuntimeError("CLIENT_ORDER_NOT_FOUND")
 async def cancel(self,order_id,symbol):
  row=await asyncio.wait_for(self.client.cancel_order(order_id,symbol),self.timeout);return self._result(row)
 async def order(self,order_id,symbol):
  row=await asyncio.wait_for(self.client.fetch_order(order_id,symbol),self.timeout);return self._result(row)
