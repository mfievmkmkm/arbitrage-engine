import time
from dataclasses import dataclass
@dataclass
class Position:
 id:int;base:str;exchange:str;direction:str;notional:float;base_qty:float;spot_entry:float;future_entry:float;opened_at:float;best_net:float=0;net:float=0;status:str="OPEN"

def open_from(op,pid=0):
 p=op["prices"];direction=op["direction"];spot=p["spot_buy"] if direction=="LONG_SPOT_SHORT_FUTURE" else p["spot_sell"];future=p["future_sell"] if direction=="LONG_SPOT_SHORT_FUTURE" else p["future_buy"]
 return Position(pid,op["base"],op["exchange"],direction,op["notional"],op["base_qty"],spot,future,time.time())
def mark(pos,op):
 p=op["prices"];q=pos.base_qty
 gross=((p["spot_sell"]-pos.spot_entry)+(pos.future_entry-p["future_buy"]))*q if pos.direction=="LONG_SPOT_SHORT_FUTURE" else ((pos.spot_entry-p["spot_buy"])+(p["future_sell"]-pos.future_entry))*q
 drag=pos.notional*(op["fee_pct"]+op["safety_pct"])/100;fund=pos.notional*op.get("funding_pct",0)/100;pos.net=gross-drag+fund;pos.best_net=max(pos.best_net,pos.net);return pos.net
