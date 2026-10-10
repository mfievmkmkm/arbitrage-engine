from app.close_recovery import plan
from app.exchange_executor import SubmitResult
class T:long_contracts=10;short_contracts=10;long_venue="a";short_venue="b"
class X:
 long_result=SubmitResult("1","PARTIAL",5);short_result=SubmitResult("2","FILLED",10)
def test_long_residual(): 
 x=plan(T(),X());assert x.required and x.venue=="a" and x.side=="sell" and x.contracts==5
