from app.crash_recovery_plan import plan
def test_crash_plan_halts_inconsistent_hedged_state():
 assert not plan("HEDGED",False,True,False,False).safe
 assert plan("HEDGED",True,True,False,False).action=="MONITOR_POSITION"
