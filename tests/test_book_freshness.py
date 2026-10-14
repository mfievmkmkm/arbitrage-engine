from app.book_freshness import check
def test_book_age_gate():
 assert check(10,9.5,1000).ok
 assert not check(10,8,1000).ok
