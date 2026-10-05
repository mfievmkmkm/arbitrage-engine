from app.system_health import build
def test_health():
 assert build(True,False,True,True,False).status=="OBSERVATION_ONLY"
 assert build(False,True,True,True,True).status=="HALTED"
 assert build(True,True,True,True,True).status=="LIVE_READY"
