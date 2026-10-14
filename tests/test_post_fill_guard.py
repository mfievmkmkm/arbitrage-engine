from types import SimpleNamespace
from app.post_fill_guard import check
def test_actual_execution_deterioration_requests_flatten():
 a=SimpleNamespace(long_price=100,short_price=100.1,long_fee=.1,short_fee=.1)
 x=check(a,1,.1,.05)
 assert not x.safe and x.action=="FLATTEN" and x.reason=="ACTUAL_NET_DETERIORATED"
def test_profitable_actual_fill_can_be_kept():
 a=SimpleNamespace(long_price=100,short_price=102,long_fee=.1,short_fee=.1)
 assert check(a,1,.5,.1).safe
