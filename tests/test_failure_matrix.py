from app.failure_matrix import cases
def test_failure_matrix_has_broad_fail_closed_coverage():
 x=cases();assert len(x)>=18 and all(not c.entry_allowed for c in x)
 assert len({c.name for c in x})==len(x)
