from app.live_coordinator import LiveCoordinator
from app.live_supervisor import LiveSupervisor
def ready():
 s=LiveSupervisor();s.ci_green=s.fee_verified=s.funding_known=s.book_fresh=s.private_verified=s.restart_clean=s.closed_e2e=True;s.unknown_orders=False;return s
def test_coordinator_allows_only_complete_evidence():
 c=LiveCoordinator(ready(),50);caps={"client_id":True,"reduce_only":True,"private_positions":True}
 assert c.admission("X","a","b",0,0,True,caps,caps).allowed
 assert not c.admission("X","a","b",1,0,True,caps,caps).allowed
