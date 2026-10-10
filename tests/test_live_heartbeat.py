from app.live_heartbeat import evaluate
def test_heartbeat_fails_on_private_feed_loss():
 assert evaluate(1,1,True,True).healthy
 assert evaluate(1,9,True,True).reason=="PRIVATE_HEARTBEAT_LOST"
