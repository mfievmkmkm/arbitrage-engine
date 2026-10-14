from app.duplicate_exposure_gate import check
from types import SimpleNamespace
def test_duplicate_symbol_pair_is_blocked():
 t=SimpleNamespace(symbol="X",long_venue="a",short_venue="b");assert not check("X","a","b",[t]).allowed
 assert not check("X","b","a",[t]).allowed
