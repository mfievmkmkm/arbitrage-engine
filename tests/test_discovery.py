from app.discovery import RotatingUniverse
def test_only_shared_symbols_and_rotation():
    u=RotatingUniverse({"a":{"X","Y","Z"},"b":{"X","Y"},"c":{"X","Q"}},10,1)
    assert u.coverage==2
    assert u.next_batch()!=u.next_batch()
