from .spot_future_vwap import vwap
def direction(buy_book,sell_book,notional,fees_pct,safety_pct):
 if not buy_book.get("asks") or not sell_book.get("bids"):return None
 qty=notional/float(buy_book["asks"][0][0]);buy=vwap(buy_book["asks"],qty);sell=vwap(sell_book["bids"],qty)
 if buy is None or sell is None:return None
 gross=(sell-buy)/buy*100;return {"buy":buy,"sell":sell,"qty":qty,"gross":gross,"net":gross-fees_pct-safety_pct}
def best(a_name,a,b_name,b,notional,fees_pct,safety_pct):
 x=direction(a,b,notional,fees_pct,safety_pct);y=direction(b,a,notional,fees_pct,safety_pct);rows=[]
 if x:rows.append({**x,"buy_venue":a_name,"sell_venue":b_name})
 if y:rows.append({**y,"buy_venue":b_name,"sell_venue":a_name})
 return max(rows,key=lambda z:z["net"]) if rows else None
