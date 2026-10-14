from .fee_schedule import DEFAULT_FUTURES_FEES

def exit_rates(long_venue,short_venue,long_liquidity="taker",short_liquidity="taker",schedule=DEFAULT_FUTURES_FEES):
 return (
  schedule.require(long_venue,long_liquidity),
  schedule.require(short_venue,short_liquidity),
)
