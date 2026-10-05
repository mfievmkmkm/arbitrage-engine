from dataclasses import dataclass
@dataclass(frozen=True)
class LivePnl:
 long_gross:float
 short_gross:float
 gross:float
 fees:float
 funding:float
 net:float
def calculate(base_qty,long_entry,short_entry,long_exit,short_exit,entry_fees=0,exit_fees=0,funding=0):
 lg=(long_exit-long_entry)*base_qty
 sg=(short_entry-short_exit)*base_qty
 gross=lg+sg
 fees=entry_fees+exit_fees
 return LivePnl(lg,sg,gross,fees,funding,gross-fees+funding)
