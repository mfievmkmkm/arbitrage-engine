from app.resume_evidence import collect
from types import SimpleNamespace
def test_resume_evidence_requires_every_runtime_source():
 i=SimpleNamespace(safe=True,reason="OK");h=SimpleNamespace(healthy=True,reason="OK");assert collect(i,True,False,h,False).safe
 assert not collect(i,True,True,h,False).safe
