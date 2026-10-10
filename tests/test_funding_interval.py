from app.funding_interval import infer
def test_unknown_interval_remains_unknown():
 assert not infer({}).known
 assert infer({"interval":8}).hours==8
