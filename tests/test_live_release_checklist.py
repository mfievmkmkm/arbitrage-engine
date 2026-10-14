from app.live_release_checklist import evaluate,CHECKS
def test_release_requires_every_evidence_item():
 x={k:True for k in CHECKS};assert evaluate(x)["allowed"];x["fees_verified"]=False;assert not evaluate(x)["allowed"]
