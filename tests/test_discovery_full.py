from app.discovery import RotatingUniverse
def test_full_universe_when_size_zero():
 u=RotatingUniverse({"a":{"X","Y"},"b":{"X","Y"}},0,1)
 assert u.coverage==2 and u.total_coverage==2
