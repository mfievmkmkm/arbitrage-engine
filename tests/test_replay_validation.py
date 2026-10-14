from app.replay_validation import validate
def test_rejects_overfit():
 x=validate({"net":10},{"net":.1,"trades":30,"profit_factor":1.2,"max_drawdown":1})
 assert not x.passed and "OUT_OF_SAMPLE_COLLAPSE" in x.reasons
def test_accepts_reasonable_oos():
 assert validate({"net":10},{"net":2,"trades":30,"profit_factor":1.3,"max_drawdown":1}).passed
