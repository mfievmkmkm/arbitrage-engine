from app.stage5_readiness import assess
def test_gate():assert not assess(99,True,True,True).ready
