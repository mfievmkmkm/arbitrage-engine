from app.acceptance_runner import run
def test_acceptance_runner_is_fail_closed():
 x=run(*([True]*14));assert x.passed
 y=run(*([True]*13+[False]));assert not y.passed and "venue_capabilities" in y.failed
