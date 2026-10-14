from app.parameter_promotion import check
def test_ai_cannot_silently_change_parameters():
 assert not check(True,True,False).allowed
 assert check(True,True,True).allowed
