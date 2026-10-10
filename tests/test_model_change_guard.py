from app.model_change_guard import check
def test_parameter_change_requires_replay_and_operator():
 assert not check("a","b",True,False).allowed
 assert check("a","b",True,True).allowed
