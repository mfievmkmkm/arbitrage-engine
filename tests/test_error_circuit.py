from app.error_circuit import ErrorCircuit
def test_repeated_errors_trip_and_success_resets_streak():
 c=ErrorCircuit(3);assert not c.failure("TIMEOUT");assert not c.failure("TIMEOUT");c.success();assert c.count==0
 assert not c.failure("API");assert not c.failure("API");assert c.failure("API") and c.tripped
