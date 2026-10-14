from app.spot_future_scanner import evaluate
def test_scanner_builds_executable_net_opportunity():
 s={"asks":[[100,1]],"bids":[[99.9,1]]};f={"asks":[[105,1]],"bids":[[104.9,1]]};x=evaluate("x","BTC","BTC/USDT","BTC/USDT:USDT",s,f,10,.2,0,.1,1);assert x["direction"]=="LONG_SPOT_SHORT_FUTURE" and x["hypothetical_edge"]>4
