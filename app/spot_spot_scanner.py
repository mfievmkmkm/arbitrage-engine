from .spot_spot_edge import calculate
from .spot_future_vwap import vwap
def evaluate(symbol,buy_venue,sell_venue,buy_book,sell_book,notional,fees_pct,safety_pct=.1):
 if not buy_book.get("asks") or not sell_book.get("bids"):return None
 qty=notional/float(buy_book["asks"][0][0]);ask=vwap(buy_book["asks"],qty);bid=vwap(sell_book["bids"],qty)
 if ask is None or bid is None:return None
 e=calculate(buy_venue,sell_venue,ask,float(buy_book["bids"][0][0]),float(sell_book["asks"][0][0]),bid,fees_pct,safety_pct)
 return {"strategy":"spot_spot","symbol":symbol,"buy":e.buy,"sell":e.sell,"gross":e.gross_pct,"net":e.net_pct,"base_qty":qty,"notional":notional}
