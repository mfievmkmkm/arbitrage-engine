from app.spot_future_campaign import status
def test_spot_future_requires_100_paper_and_replay():
 assert not status(99,True).ready
 assert not status(100,False).ready
 assert status(100,True).ready
