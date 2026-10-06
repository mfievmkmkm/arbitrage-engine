from app.strategy_registry import defaults
def test_new_strategies_default_live_off():
 x=defaults();assert x["spot_futures"].scan and not x["spot_futures"].live and not x["cex_dex"].live
