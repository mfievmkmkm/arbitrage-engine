import pytest
from app.fee_schedule import FeeSchedule,FeeRate
from app.venue_exit_cost import exit_rates

def test_venue_specific_maker_taker_rates():
 s=FeeSchedule({"a":FeeRate(.001,.002),"b":FeeRate(.003,.004)})
 assert exit_rates("a","b","maker","taker",s)==(.001,.004)

def test_unknown_fee_rate_fails_closed():
 with pytest.raises(RuntimeError,match="UNKNOWN_FEE_RATE"):
  exit_rates("unknown","binance")
