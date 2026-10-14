from app.risk import RiskGuard
def test_error_halt():
    r=RiskGuard(max_errors=3);r.on_error();r.on_error();assert r.can_open_paper();r.on_error();assert not r.can_open_paper()
def test_paper_daily_stop():
    r=RiskGuard(bankroll=50,daily_stop_pct=2);r.on_paper_close(-1.01);assert not r.can_open_paper()
