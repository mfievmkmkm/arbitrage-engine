from app.unified_runtime import UnifiedRuntime
def test_runtime_tracks_strategy_counts_and_errors():
 x=UnifiedRuntime();x.update("spot_futures",[1,2],5);x.fail("dex");s=x.snapshot();assert s["counts"]["spot_futures"]==2 and s["errors"]["dex"]==1
