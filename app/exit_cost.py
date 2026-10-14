from dataclasses import dataclass

@dataclass(frozen=True)
class ExitCost:
 fees:float
 funding:float
 buffer:float
 total_drag:float

def estimate(base_qty,long_exit,short_exit,long_fee_rate=0,short_fee_rate=0,funding=0,safety_buffer=0):
 q=max(0,float(base_qty))
 fees=q*abs(float(long_exit))*max(0,float(long_fee_rate))+q*abs(float(short_exit))*max(0,float(short_fee_rate))
 buffer=max(0,float(safety_buffer))
 return ExitCost(fees,float(funding),buffer,fees+buffer-float(funding))
