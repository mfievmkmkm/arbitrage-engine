from app.balance_guard import check
def test_reserve():
 assert check(10,5,20).allowed
 assert not check(5,5,20).allowed
