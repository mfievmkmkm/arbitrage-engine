from app.live_phases import *
def test_live_phase_progression_is_monotonic():assert can_advance(PLANNED,OPEN) and not can_advance(EXIT_SUBMITTING,OPEN)
