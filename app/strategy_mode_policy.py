LIVE_ALLOWED={"futures_futures"}
def can_real(strategy,certified,accepted):return strategy in LIVE_ALLOWED and certified and accepted
