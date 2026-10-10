from app.stage4_acceptance import assess
def test_all_required():
 assert assess(1,1,1,1,1,1,1).passed
 assert not assess(1,1,0,1,1,1,1).passed
