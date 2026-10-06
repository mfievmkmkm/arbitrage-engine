from app.recovery_budget import check
def test_recovery_extra_loss_is_capped():
 assert check(50,.1,.25).allowed
 assert not check(50,.2,.25).allowed
