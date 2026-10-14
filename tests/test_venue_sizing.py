from app.venue_sizing import size,matched
class C:
 def amount_to_precision(self,s,v):return str(round(v))
class S:
 symbol="X";contract_size=.001;amount_min=1;cost_min=None
def test_size():
 a=size(C(),S(),.01,100);assert a.contracts==10 and a.base_amount==.01
 assert matched(a,a)
