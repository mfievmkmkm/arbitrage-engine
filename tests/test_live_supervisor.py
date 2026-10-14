from app.live_supervisor import LiveSupervisor
def test_supervisor_fail_closed_then_ready():
 s=LiveSupervisor();assert not s.readiness().ready
 s.ci_green=s.fee_verified=s.funding_known=s.book_fresh=s.private_verified=s.restart_clean=s.closed_e2e=True;s.unknown_orders=False
 assert s.readiness().ready
 s.failure("API");s.failure("API");s.failure("API");assert not s.readiness().ready
