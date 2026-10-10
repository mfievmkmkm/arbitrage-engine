from app.fee_evidence_gate import evaluate as fee
from app.funding_evidence_gate import evaluate as funding
from app.book_evidence_gate import evaluate as book
def test_evidence_gates_fail_closed():
 assert not fee(None,None,10,20)[0];assert not funding(None,8,1)[0];assert not book(None,10,100,[[1,1]],[[2,1]])[0]
