from .spot_future_execution_plan import Leg,Plan
def build(open_plan):
 s=open_plan.spot;f=open_plan.future
 return Plan(Leg("spot",s.symbol,"sell" if s.side=="buy" else "buy",s.base_qty),Leg("future",f.symbol,"sell" if f.side=="buy" else "buy",f.base_qty,True))
