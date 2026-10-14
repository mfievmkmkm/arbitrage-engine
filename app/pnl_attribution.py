from dataclasses import dataclass
@dataclass(frozen=True)
class Attribution:
 spread:float;fees:float;funding:float;slippage:float;other:float;net:float

def calculate(spread,fees,funding,slippage,other=0):
 n=float(spread)-float(fees)+float(funding)-float(slippage)-float(other)
 return Attribution(spread,fees,funding,slippage,other,n)
