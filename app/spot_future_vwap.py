def vwap(levels,qty):
 rem=float(qty);total=0
 for price,amount in levels:
  take=min(rem,float(amount));total+=take*float(price);rem-=take
  if rem<=1e-12:return total/qty
 return None

def executable(spot_book,future_book,base_qty):
 return {"spot_buy":vwap(spot_book["asks"],base_qty),"spot_sell":vwap(spot_book["bids"],base_qty),"future_buy":vwap(future_book["asks"],base_qty),"future_sell":vwap(future_book["bids"],base_qty)}
