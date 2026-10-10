from app.strategy_mode_policy import can_real
def test_only_futures_futures_can_be_real_candidate():
 assert can_real("futures_futures",True,True);assert not can_real("spot_futures",True,True);assert not can_real("cex_dex",True,True)
