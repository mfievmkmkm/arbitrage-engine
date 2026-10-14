from app.latency_timeout import from_latency
def test_latency_timeout_is_bounded():
 assert from_latency(10).submit==2
 assert from_latency(10000).submit==10
