from app.exposure_monitor import calculate
def test_exposure_monitor_detects_unhedged_base():
 assert calculate(1,1,100,110).hedged
 assert not calculate(1,.8,100,110).hedged
