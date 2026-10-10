from app.tg_market_detail import render


def test_market_detail_labels_research_state():
    assert "Исследовательская оценка" in render(
        "spot_futures", {"base": "X", "hypothetical_edge": 2, "exchange": "a"}
    )
