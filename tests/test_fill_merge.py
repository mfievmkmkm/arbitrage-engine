from app.fill_merge import merge_result
from app.exchange_executor import SubmitResult
def test_fill_merge_uses_vwap_and_sums_fees():
 a=SubmitResult("1","partial",.4,100,.1);b=SubmitResult("2","closed",.6,110,.2)
 x=merge_result(a,b)
 assert abs(x.avg_price-106)<1e-12 and x.filled==1 and abs(x.fee-.3)<1e-12
def test_fill_merge_missing_positive_fill_price_fails_closed():
 import pytest
 with pytest.raises(RuntimeError,match="MISSING_RECOVERY_FILL_PRICE"):
  merge_result(SubmitResult("1","partial",.5,100,0),SubmitResult("2","closed",.5,None,0))
