from dataclasses import dataclass
@dataclass(frozen=True)
class Validation:
 passed:bool;train_net:float;test_net:float;test_trades:int;profit_factor:float;max_drawdown:float;reasons:tuple
def validate(train,test,min_test_trades=20,min_profit_factor=1.05):
 reasons=[]
 tn=float(train.get("net",0));xn=float(test.get("net",0));n=int(test.get("trades",0));pf=float(test.get("profit_factor",0));dd=float(test.get("max_drawdown",0))
 if n<min_test_trades:reasons.append("TEST_SAMPLE_TOO_SMALL")
 if xn<=0:reasons.append("TEST_NET_NONPOSITIVE")
 if pf<min_profit_factor:reasons.append("TEST_PROFIT_FACTOR_LOW")
 if tn>0 and xn/tn<.05:reasons.append("OUT_OF_SAMPLE_COLLAPSE")
 return Validation(not reasons,tn,xn,n,pf,dd,tuple(reasons))
