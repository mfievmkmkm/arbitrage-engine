from dataclasses import dataclass
@dataclass(frozen=True)
class Economics:
 allowed:bool;carry_pct:float;net_pct:float;reason:str
def calculate(rate_long_pct,rate_short_pct,holding_hours,long_interval,short_interval,roundtrip_fees_pct,basis_risk_pct,safety_pct):
 if not long_interval or not short_interval:return Economics(False,0,0,"INTERVAL_UNKNOWN")
 carry=rate_short_pct*holding_hours/short_interval-rate_long_pct*holding_hours/long_interval;net=carry-roundtrip_fees_pct-basis_risk_pct-safety_pct;return Economics(net>0,carry,net,"OK" if net>0 else "NET_NONPOSITIVE")
