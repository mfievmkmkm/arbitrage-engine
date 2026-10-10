from dataclasses import dataclass
@dataclass(frozen=True)
class SpreadShock:
 safe:bool
 widening_pct:float
 reason:str
def check(expected_spread,current_spread,max_widening_pct=.25):
 if expected_spread<=0:return SpreadShock(False,0,"EXPECTED_SPREAD_INVALID")
 widening=(current_spread-expected_spread)/expected_spread*100
 return SpreadShock(widening<=max_widening_pct,widening,"OK" if widening<=max_widening_pct else "SPREAD_SHOCK")
