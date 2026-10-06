import time
from .spot_future_edge import calculate
from .spot_future_vwap import executable

def evaluate(exchange,base,spot_symbol,future_symbol,spot_book,future_book,notional,fee_pct,funding_pct=0,safety_pct=.1,now=None):
 now=time.time() if now is None else now
 if not spot_book.get("asks") or not spot_book.get("bids") or not future_book.get("asks") or not future_book.get("bids"):return None
 qty=notional/float(spot_book["asks"][0][0]);x=executable(spot_book,future_book,qty)
 if any(v is None for v in x.values()):return None
 e=calculate(x["spot_buy"],x["spot_sell"],x["future_buy"],x["future_sell"],fee_pct,funding_pct,safety_pct)
 return {"strategy":"spot_futures","exchange":exchange,"base":base,"spot_symbol":spot_symbol,"future_symbol":future_symbol,"direction":e.direction,"executable":e.executable_pct,"hypothetical_edge":e.net_pct,"notional":notional,"base_qty":qty,"prices":x,"funding_pct":funding_pct,"fee_pct":fee_pct,"safety_pct":safety_pct,"ts":now}
