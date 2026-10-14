from app.invariants import closed
def test_never_closed_with_residual():
 assert not closed(.001,0).ok
 assert closed(0,0).ok
