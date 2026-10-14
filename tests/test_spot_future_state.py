from app.spot_future_state import transition
def test_invalid_transition_halts():
 assert transition("SCANNED","VALIDATED")=="VALIDATED"
 assert transition("SCANNED","HEDGED")=="HALTED"
