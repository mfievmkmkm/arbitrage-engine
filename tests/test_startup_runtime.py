from app.startup_runtime import evaluate
def test_no_private_is_paper():
 x=evaluate([],{},False);assert x.safe and x.mode=="PAPER"
