from app.funding_timing import window,carry_pct
def test_funding_window():
 now=1_000_000_000_000
 assert not window(now+10_000_000,60,8,now).due
 w=window(now+30_000,60,8,now);assert w.due and carry_pct(.001,.002,w)==.1
