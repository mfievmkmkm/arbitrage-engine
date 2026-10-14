LIVE_ALLOWED = {"futures_futures", "spot_futures", "spot_spot", "funding_arb"}


def can_real(
    strategy, certified, accepted, cash_accepted=False, funding_accepted=False
):
    return bool(
        strategy in LIVE_ALLOWED
        and certified is True
        and accepted is True
        and (strategy not in ("spot_futures", "spot_spot") or cash_accepted is True)
        and (strategy != "funding_arb" or funding_accepted is True)
    )
