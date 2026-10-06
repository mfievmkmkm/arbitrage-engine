from dataclasses import dataclass
@dataclass(frozen=True)
class Edge:
 buy:str;sell:str;gross_pct:float;net_pct:float

def calculate(a,b,a_ask,a_bid,b_ask,b_bid,fees_pct,safety_pct=0):
 ab=(b_bid-a_ask)/a_ask*100;ba=(a_bid-b_ask)/b_ask*100
 return Edge(a,b,ab,ab-fees_pct-safety_pct) if ab>=ba else Edge(b,a,ba,ba-fees_pct-safety_pct)
