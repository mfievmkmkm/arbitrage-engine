from app.partial_exit_gate import allowed
def test_partial_exit_only_if_replay_proves_improvement():
 assert not allowed(None);assert not allowed(4);assert allowed(5)
