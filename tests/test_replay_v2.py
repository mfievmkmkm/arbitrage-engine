from app.replay_v2 import metrics,compare
def test_metrics():
 x=metrics([2,-1,3]);assert x.net==4 and round(x.win_rate,1)==66.7 and x.max_drawdown==1
 assert compare({"a":[1],"b":[2]})[0][0]=="b"
