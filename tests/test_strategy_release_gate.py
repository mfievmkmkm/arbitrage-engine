from app.strategy_release_gate import evaluate


def test_spot_future_stays_live_locked_after_campaign():
    x = evaluate("spot_futures", True)
    assert x.scan and x.paper and not x.live


def test_dex_requires_both_scopes_and_configured_runtime():
    for missing in (
        "dedicated_acceptance",
        "wallet_acceptance",
        "dex_runtime_connected",
    ):
        inputs = dict(
            dedicated_acceptance=True,
            wallet_acceptance=True,
            dex_runtime_connected=True,
        )
        inputs[missing] = False
        assert not evaluate("cex_dex", True, **inputs).live
    assert evaluate(
        "cex_dex",
        False,
        dedicated_acceptance=True,
        wallet_acceptance=True,
        dex_runtime_connected=True,
    ).live
