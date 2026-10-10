from app.execution_watchdog import Watchdog
def test_timeout():
 w=Watchdog(2);w.start(10)
 assert not w.expired(11)
 assert w.expired(12) and w.action(False)=="RECOVER"
