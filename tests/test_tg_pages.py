from app.tg_pages import strategies, dex


class R:
    def counts(self):
        return {"futures_futures": 1, "spot_futures": 2}


def test_strategy_console_exposes_lock_state():
    x = strategies(R())
    assert "Сканер · Paper · реальная торговля" in x and "REAL 🔒" in x


def test_dex_console_never_implies_live():
    assert (
        "on-chain сделки заблокированы" in dex() and "сквозного подтверждения" in dex()
    )
