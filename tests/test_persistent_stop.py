from app.persistent_stop import Stop
from types import SimpleNamespace


def test_stop_defaults_stopped_and_resume_requires_evidence(tmp_path):
    p = str(tmp_path / "s.json")
    x = Stop(p)
    assert x.stopped
    assert not x.resume(SimpleNamespace(safe=False))
    assert x.resume(SimpleNamespace(safe=True))
    assert Stop(p).stopped
