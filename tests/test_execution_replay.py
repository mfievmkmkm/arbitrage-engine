from app.execution_replay import build,closed_net
def test_execution_replay():
 d=build([{"trade_id":"1","kind":"MARK","ts":1,"net":2},{"trade_id":"1","kind":"CLOSE","ts":2,"reason":"TARGET"}])
 assert d[0]["closed"] and closed_net(d)==[2]
