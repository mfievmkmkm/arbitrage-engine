from app.tg_incident_banner import render
from app.tg_capital_allocation import render as cap
from app.tg_live_position import render as live
from types import SimpleNamespace


def test_incident_banner_prioritizes_unknown_state():
    s = SimpleNamespace(
        unknown_orders=["x"], restart_clean=False, private_verified=False
    )
    x = render(s, SimpleNamespace(stopped=True))
    assert "Неизвестные заявки" in x and "сверка после перезапуска" in x


def test_capital_screen_states_no_withdrawals():
    assert "withdrawals are disabled" in cap(50, {"futures": 5}, 45, 5)


def test_live_card_requires_private_verified_close():
    t = SimpleNamespace(symbol="X", long_venue="a", short_venue="b")
    assert "PRIVATE VERIFIED" in live(t)
