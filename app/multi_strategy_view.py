def render(runtime):
 c=runtime.counts();return "🧭 STRATEGIES\nFutures-Futures: %s\nSpot-Futures: %s\nSpot-Spot: %s\nCEX-DEX: %s"%(c.get("futures_futures",0),c.get("spot_futures",0),c.get("spot_spot",0),c.get("cex_dex",0))
