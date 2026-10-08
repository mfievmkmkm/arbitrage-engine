from types import SimpleNamespace
from app.tg_dashboard import home, opportunities


class H:
    def snapshot(self):
        return {}


class R:
    state = SimpleNamespace(halted=False, paper_daily_pnl=1.2)


class RT:
    def counts(self):
        return {"futures_futures": 3}


def test_home_is_operator_dashboard():
    s = SimpleNamespace(
        ids=["a"], clients={"a": 1}, paused=False, last_scan=None, health=H()
    )
    p = SimpleNamespace(positions={}, max_positions=2)
    x = home(s, p, R(), RT(), SimpleNamespace(stopped=False))
    assert "ARBITRAGE ENGINE" in x and "Фьючерсы ↔ Фьючерсы" in x


def test_market_uses_executable_net():
    x = opportunities(
        [
            {
                "symbol": "X",
                "buy": "a",
                "sell": "b",
                "hypothetical_edge": 2,
                "executable": 2.3,
                "funding_pct": 0.1,
            }
        ]
    )
    assert "Оценки по стаканам" in x and "+2.000%" in x
