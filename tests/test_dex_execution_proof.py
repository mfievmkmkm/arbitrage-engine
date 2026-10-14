from app.dex_execution_proof import evaluate
from types import SimpleNamespace
def test_dex_proof_requires_every_layer():
 good=SimpleNamespace(allowed=True,reasons=());net=SimpleNamespace(allowed=True);assert evaluate(good,good,good,good,net).allowed
 bad=SimpleNamespace(allowed=False,reasons=("GAS_UNKNOWN",));assert not evaluate(good,bad,good,good,net).allowed
