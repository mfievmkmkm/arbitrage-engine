from app.tg_pages import strategies, dex


class R:
    def counts(self):
        return {"futures_futures": 1, "spot_futures": 2}


def test_strategy_console_exposes_lock_state():
    x = strategies(R())
    assert "Сканер · Paper · реальная торговля" in x and "REAL 🔒" in x


def test_dex_console_never_implies_live():
    assert (
        "firm" in dex().lower()
        and "Реальные транзакции кошелька пока не подключены" in dex()
    )
