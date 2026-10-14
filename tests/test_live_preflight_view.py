from app.live_preflight_view import render


def test_readonly_preview_does_not_claim_execution_or_profitability():
    text = render(
        dict(
            status="DATA_CHECKED",
            symbol="X<Y>",
            long_venue="a",
            short_venue="b",
            base_qty=0.04,
            long_contracts=4,
            short_contracts=4,
            long_limit=100,
            short_limit=105,
            net_edge_usd=0.1,
            required_net_usd=0.05,
            equity=50,
            daily_loss=0,
            write_authorized=False,
        )
    )
    assert "Заявки не отправляются" in text and "STOP" in text and "X&lt;Y&gt;" in text
    assert "не предоставлен" in text and "не подтверждает прибыльность" in text


def test_missing_route_and_failed_check_are_actionable_and_escaped():
    assert "Нет выбранного маршрута" in render(None)
    assert "ENTRY_PRIVATE&lt;BAD&gt;" in render({"status": "ENTRY_PRIVATE<BAD>"})
