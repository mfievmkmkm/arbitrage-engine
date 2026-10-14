from dataclasses import dataclass
@dataclass(frozen=True)
class FillPnL:
 gross:float;fees:float;funding:float;net:float
def closed(long_entry,long_exit,short_entry,short_exit,base_qty,fees=0,funding=0):
 gross=((long_exit-long_entry)+(short_entry-short_exit))*base_qty
 return FillPnL(gross,fees,funding,gross-fees+funding)
def entry_spread(long_avg,short_avg):
 return 0 if not long_avg else (short_avg-long_avg)/long_avg*100
