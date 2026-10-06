from types import SimpleNamespace
from app.tg_position_console import paper
def test_position_console_combines_strategies():
 p=SimpleNamespace(positions={});s=SimpleNamespace(positions={1:SimpleNamespace(base="X",exchange="a",direction="LONG",net=.1)});assert "Spot↔Futures     <b>1</b>" in paper(p,s)
