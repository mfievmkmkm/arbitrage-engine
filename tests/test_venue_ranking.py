from app.venue_ranking import rank
def test_incidents_and_latency_reduce_rank():
 x=rank({"a":{"net":2,"opportunities":10,"incidents":0,"latency_ms":100},"b":{"net":2,"opportunities":10,"incidents":2,"latency_ms":100}});assert x[0][0]=="a"
