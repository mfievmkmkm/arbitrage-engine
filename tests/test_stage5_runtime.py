from app.stage5_runtime import admit
class G:micro_live=True
def test_live_off_always_blocks():
 x=admit(False,G(),True,True,True,50,5,0)
 assert not x.allowed and "LIVE_DISABLED" in x.reasons
def test_budget_blocks():
 x=admit(True,G(),True,True,True,50,6,0)
 assert not x.allowed
