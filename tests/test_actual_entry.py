from app.actual_entry import build
from app.exchange_executor import SubmitResult
class L:contract_size=.1
class P:long=L();short=L()
class R:
 long_result=SubmitResult("1","FILLED",10,101,.1)
 short_result=SubmitResult("2","FILLED",10,109,.2)
def test_actual(): 
 x=build(R(),P());assert x.long_price==101 and x.short_price==109 and x.base_qty==1 and x.long_fee+x.short_fee==.3
