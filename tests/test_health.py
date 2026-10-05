from app.health import VenueHealth
def test_health_score():
    h=VenueHealth();h.success("x",12.3);h.failure("x","timeout")
    assert h.score("x")==50.0
    assert h.snapshot()["x"]["latency_ms"]==12.3
