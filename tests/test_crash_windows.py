from app.crash_window import classify
def test_crash_after_submit_unknown_halts():assert classify("ENTRY_SUBMITTING","UNKNOWN",False)=="GLOBAL_HALT"
def test_crash_after_private_hedge_restores():assert classify("HEDGED_PRIVATE_VERIFIED","HEDGED",False)=="RESTORE_FROM_PRIVATE"
def test_crash_during_exit_flat_marks_verified():assert classify("EXIT_SUBMITTING","FLAT",True)=="MARK_CLOSED_VERIFIED"
