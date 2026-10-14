from app.private_failure_action import action
def test_missing_private_state_halts_if_exposure_possible():assert action(True,False)=="GLOBAL_HALT_EXPOSURE_UNKNOWN"
