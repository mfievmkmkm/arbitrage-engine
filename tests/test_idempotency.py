from app.idempotency import IdempotencyGuard
def test_duplicate_blocked():
 g=IdempotencyGuard();assert g.begin("x");assert not g.begin("x");g.finish("x");assert not g.begin("x")
