from app.order_evidence import validate
from app.exchange_executor import SubmitResult
def test_filled_order_requires_id_and_price():
 assert not validate("x",SubmitResult("","closed",1,100,0),1).safe
 assert not validate("x",SubmitResult("1","closed",1,None,0),1).safe
 assert validate("x",SubmitResult("1","closed",1,100,0),1).safe
