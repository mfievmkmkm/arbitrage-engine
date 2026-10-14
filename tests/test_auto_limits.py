from app.auto_limits import Limits,validate
def test_initial_auto_limits_cannot_expand_micro_live():
 assert validate(Limits())
 assert not validate(Limits(max_open=2))
