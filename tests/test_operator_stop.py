from app.operator_stop import StopController
def test_stop_blocks_new_entries_until_explicit_resume():
 s=StopController();s.stop();assert not s.allow_new_entries();s.resume();assert s.allow_new_entries()
