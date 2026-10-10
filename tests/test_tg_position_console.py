from types import SimpleNamespace
from app.tg_position_console import paper


def test_position_console_combines_strategies():
    p = SimpleNamespace(positions={})
    s = SimpleNamespace(
        positions={
            1: SimpleNamespace(
                base="X",
                exchange="a",
                direction="LONG",
                net=0.1,
                last_mark=None,
                base_qty=1,
            )
        }
    )
    assert "Спот ↔ Фьючерсы: <b>1</b>" in paper(p, s)
