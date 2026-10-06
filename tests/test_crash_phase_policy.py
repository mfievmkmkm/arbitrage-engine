from app.crash_phase_policy import decide
def test_every_critical_phase_has_restart_action():
 for p in ("PRE_SUBMIT","INTENTS_PERSISTED","ENTRY_SUBMITTED","ENTRY_FILLED","HEDGED","EXIT_INTENTS_PERSISTED","EXIT_SUBMITTED","EXIT_FILLED","CLOSED_VERIFIED"):assert not decide(p).action.startswith("HALT_UNKNOWN")
