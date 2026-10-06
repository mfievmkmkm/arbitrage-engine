import pytest
from app.live_fee_evidence import FeeEvidence,live_rate
def test_live_fee_requires_verified_source():
 with pytest.raises(RuntimeError):live_rate(FeeEvidence(.001,"default",False))
 assert live_rate(FeeEvidence(.001,"exchange/account",True))==.001
