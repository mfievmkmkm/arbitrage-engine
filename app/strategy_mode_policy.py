LIVE_ALLOWED = {"futures_futures", "spot_futures"}


def can_real(strategy, certified, accepted, cash_accepted=False):
    return bool(
        strategy in LIVE_ALLOWED
        and certified is True
        and accepted is True
        and (strategy != "spot_futures" or cash_accepted is True)
    )
