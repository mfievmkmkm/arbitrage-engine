from app.dex_acceptance import evaluate,REQUIRED
def test_dex_acceptance_requires_every_proof():
 assert evaluate({x:True for x in REQUIRED})[0]
 assert not evaluate({x:True for x in REQUIRED if x!="gas"})[0]
