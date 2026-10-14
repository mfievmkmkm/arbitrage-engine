from app.close_acceptance import evaluate
from types import SimpleNamespace
def test_close_commit_requires_result_and_private_flat():
 x=SimpleNamespace(closed=True,status="CLOSED_VERIFIED",result=object());assert evaluate(x,True,True).accepted
 assert not evaluate(x,True,False).accepted
