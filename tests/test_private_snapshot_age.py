from app.private_snapshot_age import check
def test_stale_private_snapshot_is_untrusted():
 assert check(10,9,3).trusted
 assert not check(10,1,3).trusted
